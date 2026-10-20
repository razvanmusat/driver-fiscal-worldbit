"""Reîncercarea AUTOMATĂ a eșecurilor trecătoare documentate de drivere: fără operator, în agent."""
import time
import unittest

from tests.fakes import FakeFiscool, FakeFisco, fiscool_cfg, fisco_cfg, free_port, make_runner, receipt_json, wait_status

TRECATOR = "Printer status is not known yet"   # unul dintre motivele documentate în manualul Fisco (2.2)


class FiscoReincercareTest(unittest.TestCase):
    def setUp(self):
        self.fisco = FakeFisco().start()
        self.addCleanup(self.fisco.stop)

    def porneste(self, **cfg):
        runner = make_runner({**fisco_cfg(self.fisco.url), **cfg})
        runner.submit_receipt(receipt_json())
        return runner, wait_status(runner, "r1", ("printed", "failed", "uncertain"))

    def test_casa_nepregatita_trecator_se_reincearca_singura_si_tipareste_odata(self):
        self.fisco.ready_error = TRECATOR
        self.fisco.not_ready_times = 2
        runner, row = self.porneste()
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(row["attempt"], 3)                                 # două încercări automate, a treia a reușit
        self.assertEqual(self.fisco.print_count, 1)                         # un singur bon
        self.assertEqual(self.fisco.posts[0]["external_id"], "r1-3")        # cheie nouă la fiecare încercare
        self.assertIsNone(row["error"])                                     # succesul șterge eroarea informativă

    def test_in_timpul_reincercarii_jobul_ramane_in_lucru_cu_eroarea_si_numaratoarea_vizibile(self):
        self.fisco.ready_error = TRECATOR
        self.fisco.not_ready_times = 100
        runner = make_runner({**fisco_cfg(self.fisco.url), "auto_retry": {"max": 3, "delay_s": 0.4}})
        runner.submit_receipt(receipt_json())
        row = None
        for _ in range(100):
            row = runner.store.get("r1")
            if row["error"] and row["error"].get("auto_retry"):
                break
            time.sleep(0.01)
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["error"]["auto_retry"]["max"], 3)
        self.assertIn(TRECATOR, row["error"]["message"])

    def test_dupa_ultima_reincercare_apare_eroarea_finala(self):
        self.fisco.ready_error = TRECATOR
        self.fisco.not_ready_times = 100
        runner, row = self.porneste()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error"]["code"], "NOT_CONNECTED")
        self.assertEqual(row["attempt"], 4)                                 # 1 + 3 reîncercări
        self.assertEqual(row["error"]["auto_retry"], {"count": 3, "max": 3, "epuizat": True})
        self.assertEqual(self.fisco.posts, [])                              # nimic nu a ajuns la casă

    def test_depasirea_celor_24h_nu_e_trecatoare_deci_nu_se_reincearca(self):
        self.fisco.ready = False   # motivul implicit: „Depasire 24h Raport Z”
        runner, row = self.porneste()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["attempt"], 1)
        self.assertNotIn("auto_retry", row["error"])
        self.assertIn("raportului Z", row["error"]["resolution"])

    def test_fisco_oprit_conexiune_refuzata_se_reincearca_si_apoi_esueaza(self):
        runner = make_runner(fisco_cfg(f"http://127.0.0.1:{free_port()}"))
        runner.submit_receipt(receipt_json())
        row = wait_status(runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["attempt"], 4)
        self.assertTrue(row["error"]["auto_retry"]["epuizat"])

    def test_comanda_respinsa_nu_se_reincearca_automat(self):
        self.fisco.mode = "reject"
        runner, row = self.porneste()
        self.assertEqual((row["status"], row["attempt"], row["error"]["code"]), ("failed", 1, "REJECTED"))
        self.assertEqual(len(self.fisco.posts), 1)

    def test_tiparire_esuata_nu_se_reincearca_automat(self):
        self.fisco.mode = "fail"
        runner, row = self.porneste()
        self.assertEqual((row["status"], row["attempt"]), ("failed", 1))
        self.assertEqual(self.fisco.print_count, 1)

    def test_reincercarile_automate_se_pot_opri_din_setari(self):
        self.fisco.ready_error = TRECATOR
        self.fisco.not_ready_times = 1
        runner, row = self.porneste(auto_retry={"max": 0, "delay_s": 0.01})
        self.assertEqual((row["status"], row["attempt"]), ("failed", 1))


class FiscoolReincercareTest(unittest.TestCase):
    def setUp(self):
        self.fiscool = FakeFiscool().start()
        self.addCleanup(self.fiscool.stop)
        self.runner = make_runner(fiscool_cfg(self.fiscool.url))

    def ruleaza(self):
        self.runner.submit_receipt(receipt_json())
        return wait_status(self.runner, "r1", ("printed", "failed", "uncertain"))

    def test_casa_ocupata_505_se_reincearca_cu_cheie_noua_si_tipareste_odata(self):
        self.fiscool.error = (400, 505, "Dispozitivul este ocupat. Încercați din nou.")
        self.fiscool.error_times = 2
        row = self.ruleaza()
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual([k for k, _ in self.fiscool.requests], ["r1", "r1-2", "r1-3"])
        self.assertEqual(self.fiscool.executed, 1)

    def test_conexiune_1004_se_reincearca_singura(self):
        self.fiscool.error = (503, 1004, "Dispozitivul refuză conexiunea")
        self.fiscool.error_times = 1
        row = self.ruleaza()
        self.assertEqual((row["status"], row["attempt"]), ("printed", 2))
        self.assertEqual(self.fiscool.executed, 1)

    def test_casa_in_curs_de_conectare_se_asteapta(self):
        runner = make_runner({**fiscool_cfg(self.fiscool.url), "auto_retry": {"max": 10, "delay_s": 0.1}})
        self.fiscool.device_status = "Connecting"
        runner.submit_receipt(receipt_json())
        time.sleep(0.25)
        self.fiscool.device_status = "Connected"
        row = wait_status(runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "printed", row)
        self.assertGreaterEqual(row["attempt"], 2)
        self.assertEqual(self.fiscool.executed, 1)

    def test_505_permanent_epuizeaza_reincercarile_si_esueaza_cu_mesajul_driverului(self):
        self.fiscool.error = (400, 505, "Dispozitivul este ocupat. Încercați din nou.")
        row = self.ruleaza()
        self.assertEqual((row["status"], row["attempt"]), ("failed", 4))
        self.assertEqual(row["error"]["driver_name"], "DeviceBusy")
        self.assertEqual(self.fiscool.executed, 0)

    def test_erorile_care_cer_o_actiune_nu_se_reincearca_automat(self):
        for http, cod in ((400, 400), (400, 530), (400, 544), (400, 540), (400, 601), (503, 500)):
            with self.subTest(cod=cod):
                fiscool = FakeFiscool().start()
                self.addCleanup(fiscool.stop)
                runner = make_runner(fiscool_cfg(fiscool.url))
                fiscool.error = (http, cod, "mesaj")
                runner.submit_receipt(receipt_json())
                row = wait_status(runner, "r1", ("printed", "failed", "uncertain"))
                self.assertEqual((row["status"], row["attempt"]), ("failed", 1))
                self.assertEqual(len(fiscool.requests), 1)

    def test_timeout_408_nu_se_reincearca_ci_se_lamureste_pe_aceeasi_cheie(self):
        self.fiscool.error = (408, 408, "Dispozitivul nu a răspuns la timp")
        self.fiscool.error_times = 1
        row = self.ruleaza()
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(row["attempt"], 1)
        self.assertEqual([k for k, _ in self.fiscool.requests], ["r1"])


if __name__ == "__main__":
    unittest.main()
