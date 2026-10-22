"""Cheia de idempotență către FisCool la reluarea unei plăți.
Fără răspuns (răspuns pierdut, termen depășit, agent repornit): ACEEAȘI cheie, ca bonul care a ieșit totuși să nu se tipărească a doua oară.
Cu răspuns de eroare al driverului (FisCool memorează eroarea pe cheie): cheie nouă, altfel aceeași eroare ar reveni la nesfârșit."""
import unittest

from tests.fakes import FakeFiscool, FakeFisco, fisco_cfg, fiscool_cfg, make_runner, receipt_json, wait_status


class CheieLaReluareTest(unittest.TestCase):
    def setUp(self):
        self.fiscool = FakeFiscool().start()
        self.addCleanup(self.fiscool.stop)
        self.runner = make_runner(fiscool_cfg(self.fiscool.url))

    def chei(self):
        return [k for k, _ in self.fiscool.requests]

    def pierde_raspunsul(self, ext="r1"):
        self.fiscool.mode = "drop_after"
        self.runner.submit_receipt(receipt_json(ext))
        return wait_status(self.runner, ext, ("uncertain",))

    def abandoneaza(self, ext="r1"):
        """Termenul trece înainte de orice lămurire (abandonul vine înaintea lămuririi)."""
        self.runner.store.update(ext, started_at=1.0)
        row = self.runner.get(ext)
        self.assertEqual((row["status"], row["error"]["code"]), ("failed", "NOT_CONFIRMED"), row)
        return row

    def test_dupa_abandon_bonul_iesit_totusi_nu_se_tipareste_a_doua_oara(self):
        self.pierde_raspunsul()
        self.abandoneaza()
        row, problema = self.runner.retry("r1", False)    # NOT_CONFIRMED e retry_safe: fara confirmare
        self.assertIsNone(problema)
        row = wait_status(self.runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(self.fiscool.executed, 1)                      # un singur bon
        self.assertEqual(set(self.chei()), {"r1"})                      # aceeasi cheie, fara sufix
        self.assertEqual(row["attempt"], 2)                             # incercarea creste (aplicatia o vede), cheia nu
        self.assertEqual(row["result"]["slip_number"], "1234")          # rezultatul primului bon

    def test_dupa_repornirea_agentului_reluarea_pastreaza_cheia(self):
        self.pierde_raspunsul()
        self.runner.recover()           # nu schimba nimic aici; jobul e deja incert
        self.runner.store.update("r1", status="uncertain")
        row, problema = self.runner.retry("r1", True)    # incert: cere confirmarea operatorului
        self.assertIsNone(problema)
        row = wait_status(self.runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(self.fiscool.executed, 1)
        self.assertEqual(set(self.chei()), {"r1"})

    def test_dupa_eroare_a_driverului_cheia_este_noua(self):
        self.fiscool.error = (503, 500, "Lipsă hârtie în imprimantă.")
        self.runner.submit_receipt(receipt_json("r1"))
        row = wait_status(self.runner, "r1", ("failed", "uncertain", "printed"))
        self.assertEqual(row["status"], "failed", row)
        self.assertIn("driver_code", row["error"])
        self.fiscool.error = None                          # hartia pusa
        row, problema = self.runner.retry("r1", True)
        self.assertIsNone(problema)
        row = wait_status(self.runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "printed", row)    # cu aceeasi cheie ar fi intors eroarea memorata
        self.assertEqual(self.chei(), ["r1", "r1-2"])
        self.assertEqual(self.fiscool.executed, 1)

    def test_cheia_ramane_unica_dupa_o_reluare_fara_raspuns_urmata_de_o_eroare(self):
        self.pierde_raspunsul()
        self.abandoneaza()
        self.fiscool.cache.clear()                         # FisCool nu mai stie cheia r1
        self.fiscool.error = (503, 500, "Lipsă hârtie în imprimantă.")
        self.runner.retry("r1", False)                     # incercarea 2, cheia r1
        row = wait_status(self.runner, "r1", ("failed", "printed", "uncertain"))
        self.assertEqual(row["status"], "failed", row)
        self.fiscool.error = None
        self.runner.retry("r1", True)                      # incercarea 3, dupa eroare: cheie noua
        row = wait_status(self.runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual(row["status"], "printed", row)
        self.assertEqual(self.chei(), ["r1", "r1", "r1-3"])


class FiscoNeschimbatTest(unittest.TestCase):
    def test_fisco_isi_pastreaza_cheia_noua_la_reluare(self):
        fisco = FakeFisco().start()
        self.addCleanup(fisco.stop)
        fisco.polls_until_printed = 10 ** 6
        runner = make_runner({**fisco_cfg(fisco.url, poll_timeout_s=0.1), "max_wait_s": 1})
        runner.submit_receipt(receipt_json("r1"))
        wait_status(runner, "r1", ("uncertain",))
        runner.store.update("r1", started_at=1.0)
        self.assertEqual(runner.get("r1")["error"]["code"], "NOT_CONFIRMED")
        fisco.polls_until_printed = 1
        runner.retry("r1", False)
        wait_status(runner, "r1", ("printed", "failed", "uncertain"))
        self.assertEqual([p["external_id"] for p in fisco.posts], ["r1", "r1-2"])


if __name__ == "__main__":
    unittest.main()
