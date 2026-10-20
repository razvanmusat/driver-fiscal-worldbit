"""Toate mesajele pe care le oferă driverele ajung neschimbate în starea jobului (și de acolo în Dressbit)."""
import json
import unittest

from worldbit_fiscal.drivers import catalog_fisco, catalog_fiscool
from worldbit_fiscal.drivers.fiscool import FiscoolDriver
from tests.fakes import (FakeFiscool, FakeFisco, fiscool_cfg, fisco_cfg, make_runner, receipt_json, resolve_now, wait_status)

COD_FISCOOL_DOCUMENTATE = (
    [400, 404, 408] + list(range(1000, 1016)) + list(range(500, 510)) + list(range(520, 526)) + list(range(530, 546))
    + [550, 551, 552, 599] + list(range(600, 612)) + list(range(2001, 2006)))


class CatalogTest(unittest.TestCase):
    def test_fiscool_toate_codurile_din_documentatie(self):
        self.assertEqual(sorted(catalog_fiscool.CODURI), sorted(COD_FISCOOL_DOCUMENTATE))
        for cod, (nume, descriere, rezolvare) in catalog_fiscool.CODURI.items():
            with self.subTest(cod=cod):
                self.assertTrue(nume and descriere and rezolvare)

    def test_fisco_toate_codurile_api_din_manual(self):
        self.assertEqual(sorted(catalog_fisco.COD_API), sorted([
            "INVALID_JSON", "EMPTY_BODY", "CONVERSION_ERROR", "PRINTER_NOT_READY", "QUEUE_ERROR", "JOB_NOT_FOUND",
            "PRINT_FAILED", "METHOD_NOT_ALLOWED", "SETTINGS_ERROR", "INTERNAL_ERROR", "LAST_RECEIPT_NOT_FOUND"]))
        self.assertEqual(sorted(catalog_fisco.STATUS_JOB), ["failed", "not_found", "printed", "processing", "queued"])
        self.assertEqual(len(catalog_fisco.MOTIVE_RESPINS), 4)

    def test_fiscool_orice_cod_documentat_ajunge_cu_numele_descrierea_si_rezolvarea_lui(self):
        for cod, (nume, descriere, rezolvare) in catalog_fiscool.CODURI.items():
            with self.subTest(cod=cod):
                eroare = FiscoolDriver._error(400, {"status": cod, "error": descriere, "receipt": "CHITANTA"})
                d = eroare.to_dict()
                self.assertEqual(d["driver_code"], str(cod))
                self.assertEqual(d["driver_name"], nume)
                self.assertEqual(d["driver_description"], descriere)
                self.assertEqual(d["resolution"], rezolvare)
                self.assertEqual(d["receipt"], "CHITANTA")
                self.assertEqual(d["driver_response"]["status"], cod)
                self.assertEqual(d["driver_response"]["http_status"], 400)
                self.assertIn(descriere, d["message"])
                self.assertEqual(d["code"], f"FISCOOL_{cod}")

    def test_fiscool_cod_necunoscut_in_documentatie_trece_totusi_cu_tot_ce_a_spus_driverul(self):
        d = FiscoolDriver._error(500, {"status": 777, "error": "Eroare noua"}).to_dict()
        self.assertEqual(d["driver_code"], "777")
        self.assertNotIn("driver_name", d)
        self.assertIn("Eroare noua", d["message"])
        self.assertFalse(d["retry_safe"])


class MesajeFiscoTest(unittest.TestCase):
    def setUp(self):
        self.fisco = FakeFisco().start()
        self.addCleanup(self.fisco.stop)
        self.runner = make_runner(fisco_cfg(self.fisco.url))

    def esec(self, mod=None, ready=True):
        self.fisco.mode = mod or "normal"
        self.fisco.ready = ready
        self.runner.submit_receipt(receipt_json())
        return wait_status(self.runner, "r1", ("failed", "uncertain", "printed"))

    def test_succes_pastreaza_raspunsul_complet(self):
        row = self.esec()
        raw = row["result"]["raw"]
        self.assertEqual(raw["status"], "printed")
        self.assertEqual(raw["job_id"], "http_0001")
        self.assertEqual(raw["result"]["SlipNumber"], "6001")
        self.assertEqual(raw["result"]["CommandName"], "receipt_Fiscal_Close")
        self.assertEqual(raw["result"]["ErrorCode"], "0")

    def test_cerere_respinsa_poarta_codul_api_descrierea_si_raspunsul(self):
        e = self.esec("reject")["error"]
        self.assertEqual(e["code"], "REJECTED")
        self.assertEqual(e["driver_code"], "CONVERSION_ERROR")
        self.assertEqual(e["driver_description"], "Comenzile nu au putut fi procesate.")
        self.assertEqual(e["message"], "#Linia 2 | invalid")
        self.assertEqual(e["driver_response"]["error"]["code"], "CONVERSION_ERROR")
        self.assertFalse(e["driver_response"]["ok"])

    def test_tiparire_esuata_poarta_mesajul_intreg_cu_liniile_marcate(self):
        e = self.esec("fail")["error"]
        self.assertEqual(e["driver_code"], "PRINT_FAILED")
        self.assertEqual(e["driver_description"], "Jobul a fost procesat, dar printarea a eșuat.")
        self.assertEqual(e["message"], "#Linia 2 | eroare")
        self.assertEqual(e["driver_response"]["status"], "failed")

    def test_casa_nepregatita_24h_poarta_starea_motivul_si_rezolvarea_documentata(self):
        e = self.esec(ready=False)["error"]
        self.assertEqual(e["code"], "NOT_CONNECTED")
        self.assertIn("Depasire 24h Raport Z", e["message"])
        self.assertEqual(e["driver_name"], "not_ready")
        self.assertEqual(e["driver_description"], "Imprimanta sau driverul nu este pregătit.")
        self.assertIn("raportului Z", e["resolution"])
        self.assertEqual(e["driver_response"]["printer"]["ready"], False)

    def test_status_job_lamurit_dupa_pierderea_raspunsului_pastreaza_tot(self):
        self.fisco.mode = "drop_after"
        self.runner.submit_receipt(receipt_json())
        wait_status(self.runner, "r1", ("uncertain",))
        self.fisco.mode = "normal"
        row = resolve_now(self.runner, "r1")
        self.assertEqual(row["status"], "printed")
        self.assertEqual(row["result"]["raw"]["result"]["nZrep"], "430")


class MesajeFiscoolTest(unittest.TestCase):
    def setUp(self):
        self.fiscool = FakeFiscool().start()
        self.addCleanup(self.fiscool.stop)
        self.runner = make_runner(fiscool_cfg(self.fiscool.url))

    def ruleaza(self, **kw):
        self.runner.submit_receipt(receipt_json(**kw))
        return wait_status(self.runner, "r1", ("failed", "uncertain", "printed"))

    def test_succes_bon_poarta_mesajul_driverului_si_raspunsul_complet(self):
        row = self.ruleaza()
        self.assertEqual(row["result"]["message"], "Receipt printed")
        raw = row["result"]["raw"]
        self.assertEqual(raw["msg"], "Receipt printed")
        self.assertEqual(raw["slip_number"], 1234)
        self.assertEqual(raw["z_report_number"], 45)
        self.assertEqual(raw["fiscal_receipt_count"], 1050)
        self.assertIn("bon_id", raw)

    def test_succes_raport_poarta_mesajul_si_totalurile(self):
        self.runner.submit_report("z-1", "Z")
        row = wait_status(self.runner, "z-1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["result"]["message"], "Report printed")
        self.assertEqual(row["result"]["raw"]["grand_total_sell"], 12345)
        self.assertEqual(row["result"]["raw"]["payment_totals"][0]["method"], "cash")

    def test_lipsa_hartie_poarta_numele_descrierea_si_rezolvarea_din_documentatie(self):
        self.fiscool.error = (503, 500, "Lipsă hârtie în imprimantă.")
        e = self.ruleaza()["error"]
        self.assertEqual(e["driver_code"], "500")
        self.assertEqual(e["driver_name"], "NoPaper")
        self.assertEqual(e["driver_description"], "Lipsă hârtie în imprimantă.")
        self.assertEqual(e["resolution"], "Încărcați o rolă de hârtie")
        self.assertEqual(e["driver_response"], {"status": 500, "error": "Lipsă hârtie în imprimantă.", "http_status": 503})
        self.assertIn("Lipsă hârtie în imprimantă.", e["message"])

    def test_refuz_pos_poarta_chitanta_de_refuz(self):
        self.fiscool.error = (400, 600, "Tranzacție POS refuzată.", "CARD REFUZAT\nCOD 05")
        e = self.ruleaza(card=100)["error"]
        self.assertEqual(e["driver_name"], "PosDeclinedGeneric")
        self.assertEqual(e["receipt"], "CARD REFUZAT\nCOD 05")
        self.assertEqual(e["driver_response"]["receipt"], "CARD REFUZAT\nCOD 05")
        self.assertTrue(e["retry_safe"])

    def test_casa_deconectata_poarta_starea_si_intrarea_driverului(self):
        self.fiscool.device_status = "Disconnected"
        e = self.ruleaza()["error"]
        self.assertEqual(e["code"], "NOT_CONNECTED")
        self.assertEqual(e["driver_name"], "Disconnected")
        self.assertEqual(e["driver_response"]["status"], "Disconnected")

    def test_eroare_tranzitorie_incerta_poarta_tot_odata_cu_starea(self):
        self.fiscool.error = (408, 408, "Dispozitivul nu a răspuns la timp")
        row = self.ruleaza()
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(row["error"]["driver_name"], "Timeout")
        self.assertEqual(row["error"]["resolution"], "Cauze frecvente: dispozitivul nu a răspuns la timp")


if __name__ == "__main__":
    unittest.main()
