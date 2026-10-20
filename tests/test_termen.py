"""Termenul total de așteptare: un bon neconfirmat ca emis în 30 s (config `max_wait_s`) nu mai e „în așteptare" — devine eșec cu retrimitere
sigură, ca plata să se poată relua normal. Înregistrarea tehnică rămâne în agent."""
import os
import sqlite3
import tempfile
import time
import unittest

from worldbit_fiscal.state import Store
from tests.fakes import FakeFiscool, FakeFisco, fiscool_cfg, fisco_cfg, make_runner, receipt_json, wait_status


class TermenFiscoTest(unittest.TestCase):
    def setUp(self):
        self.fisco = FakeFisco().start()
        self.addCleanup(self.fisco.stop)
        self.fisco.polls_until_printed = 10 ** 6   # jobul e acceptat dar nu se termină niciodată (casa nu-l procesează)

    def runner(self, **cfg):
        base = fisco_cfg(self.fisco.url, poll_timeout_s=0.1)
        return make_runner({**base, "max_wait_s": 1, **cfg})

    def test_inainte_de_termen_jobul_ramane_incert(self):
        runner = self.runner()
        runner.submit_receipt(receipt_json())
        wait_status(runner, "r1", ("uncertain",))
        self.assertEqual(runner.get("r1")["status"], "uncertain")

    def test_dupa_termen_devine_esec_cu_retrimitere_sigura_si_mesaj_clar(self):
        runner = self.runner()
        runner.submit_receipt(receipt_json())
        wait_status(runner, "r1", ("uncertain",))
        time.sleep(1.1)
        row = runner.get("r1")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error"]["code"], "NOT_CONFIRMED")
        self.assertTrue(row["error"]["retry_safe"])
        self.assertIn("nu este considerat emis", row["error"]["message"])
        self.assertEqual(row["error"]["ultima_eroare"]["code"], "TIMEOUT")   # ce se întâmplase, păstrat pentru audit

    def test_inregistrarea_jobului_abandonat_ramane_in_agent_si_isi_pastreaza_job_id_ul(self):
        runner = self.runner()
        runner.submit_receipt(receipt_json())
        wait_status(runner, "r1", ("uncertain",))
        time.sleep(1.1)
        row = runner.get("r1")
        self.assertEqual(row["fiscal_job_id"], "http_0001")
        # job_id-ul rămâne atribuit lui: reconcilierea unui job următor nu-l poate adopta din greșeală
        self.assertTrue(runner.store.job_id_in_use("http_0001", "altul"))

    def test_dupa_abandon_plata_se_poate_relua_normal(self):
        runner = self.runner()
        runner.submit_receipt(receipt_json())
        wait_status(runner, "r1", ("uncertain",))
        time.sleep(1.1)
        runner.get("r1")
        self.fisco.polls_until_printed = 1
        row, problem = runner.retry("r1", False)    # retry_safe: fără nicio confirmare
        self.assertIsNone(problem)
        row = wait_status(runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual((row["status"], row["attempt"]), ("printed", 2), row)

    def test_raportul_are_termen_mai_lung(self):
        runner = self.runner(max_wait_report_s=60)
        runner.submit_report("z-1", "Z")
        wait_status(runner, "z-1", ("uncertain",))
        time.sleep(1.1)
        self.assertEqual(runner.get("z-1")["status"], "uncertain")

    def test_reincercarile_automate_se_opresc_la_termen(self):
        self.fisco.ready_error = "Printer status is not known yet"
        self.fisco.not_ready_times = 10 ** 6
        runner = make_runner({**fisco_cfg(self.fisco.url), "max_wait_s": 1, "auto_retry": {"max": 50, "delay_s": 0.3}})
        pornit = time.time()
        runner.submit_receipt(receipt_json())
        row = wait_status(runner, "r1", ("failed", "printed", "uncertain"), timeout=10)
        self.assertEqual(row["status"], "failed")
        self.assertLess(time.time() - pornit, 3)
        self.assertLess(row["attempt"], 10)   # nu a mers până la 50
        self.assertEqual(self.fisco.posts, [])

    def test_plata_cu_cardul_nelamurita_nu_se_abandoneaza(self):
        runner = self.runner()
        runner.store.create("p1", "receipt", receipt_json("p1"))
        runner.store.update("p1", status="uncertain", state={"stage": "pos"}, started_at=time.time() - 1000)
        self.assertEqual(runner.get("p1")["status"], "uncertain")


class TermenFiscoolTest(unittest.TestCase):
    def test_eroare_tranzitorie_408_persistenta_devine_esec_dupa_termen(self):
        fiscool = FakeFiscool().start()
        self.addCleanup(fiscool.stop)
        fiscool.error = (408, 408, "Dispozitivul nu a răspuns la timp")
        runner = make_runner({**fiscool_cfg(fiscool.url), "max_wait_s": 1})
        runner.submit_receipt(receipt_json())
        wait_status(runner, "r1", ("uncertain",))
        self.assertEqual(runner.get("r1")["status"], "uncertain")
        time.sleep(1.1)
        row = runner.get("r1")
        self.assertEqual((row["status"], row["error"]["code"], row["error"]["retry_safe"]), ("failed", "NOT_CONFIRMED", True))
        self.assertEqual(row["error"]["ultima_eroare"]["driver_name"], "Timeout")
        self.assertEqual(fiscool.executed, 0)


class MigrareBazaVecheTest(unittest.TestCase):
    def test_o_baza_creata_inainte_de_termen_primeste_coloana_started_at(self):
        director = tempfile.mkdtemp()
        cale = os.path.join(director, "jobs.sqlite3")
        db = sqlite3.connect(cale)
        db.execute("""CREATE TABLE jobs (external_id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1,
                      payload TEXT NOT NULL, fiscal_job_id TEXT, state TEXT NOT NULL DEFAULT '{}', pos TEXT, result TEXT, error TEXT,
                      last_resolve REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
        db.execute("INSERT INTO jobs (external_id, kind, status, payload, created_at, updated_at) VALUES ('vechi','receipt','printed','{}',1234.5,1234.5)")
        db.commit()
        db.close()
        store = Store(cale)
        self.assertEqual(store.get("vechi")["started_at"], 1234.5)
        store.close()


if __name__ == "__main__":
    unittest.main()
