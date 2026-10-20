"""payment_key: identitatea PLĂȚII, stabilă între joburile aceluiași bon. Un job nou (external_id nou) cu un payment_key deja cunoscut nu
mai tipărește: îl urmează pe cel care execută plata. Închide dublul print de după o anulare fără dovadă că bonul nu a ieșit
(net căzut, agent inaccesibil, fereastră închisă)."""
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest

from worldbit_fiscal.contract import InvalidPayload
from worldbit_fiscal.runner import Runner, public_job
from worldbit_fiscal.state import Store
from tests.fakes import (FakeFiscool, FakeFisco, fiscool_cfg, fisco_cfg, make_runner, receipt_json, resolve_now,
                         wait_status)


def cu_cheie(external_id, payment_key, **kw):
    corp = json.loads(receipt_json(external_id, **kw))
    if payment_key is not None:
        corp["payment_key"] = payment_key
    return json.dumps(corp)


class PaymentKeyFiscoolTest(unittest.TestCase):
    def setUp(self):
        self.fiscool = FakeFiscool().start()
        self.addCleanup(self.fiscool.stop)
        self.runner = make_runner(fiscool_cfg(self.fiscool.url))

    def test_job_nou_cu_acelasi_payment_key_dupa_tiparire_nu_mai_tipareste(self):
        self.runner.submit_receipt(cu_cheie("e1", "p1"))
        printed = wait_status(self.runner, "e1", ("printed",))
        row, created = self.runner.submit_receipt(cu_cheie("e2", "p1"))
        self.assertFalse(created)
        self.assertEqual(row["external_id"], "e2")
        self.assertEqual(row["status"], "printed")
        self.assertEqual(row["result"]["slip_number"], printed["result"]["slip_number"])
        self.assertEqual(row["alias_of"], "e1")
        self.assertEqual(self.fiscool.executed, 1)
        self.assertEqual(len(self.fiscool.requests), 1)   # nici măcar o a doua cerere către FisCool

    def test_get_pe_job_oglinda_arata_starea_celui_care_executa(self):
        self.runner.submit_receipt(cu_cheie("e1", "p1"))
        wait_status(self.runner, "e1", ("printed",))
        self.runner.submit_receipt(cu_cheie("e2", "p1"))
        vazut = self.runner.get("e2")
        self.assertEqual((vazut["external_id"], vazut["status"]), ("e2", "printed"))
        publicat = public_job(vazut, "fiscool", "agent-1")
        self.assertEqual((publicat["external_id"], publicat["status"], publicat["alias_of"]), ("e2", "printed", "e1"))

    def test_acelasi_external_id_retrimis_pe_oglinda_ramane_idempotent(self):
        self.runner.submit_receipt(cu_cheie("e1", "p1"))
        wait_status(self.runner, "e1", ("printed",))
        self.runner.submit_receipt(cu_cheie("e2", "p1"))
        row, created = self.runner.submit_receipt(cu_cheie("e2", "p1"))
        self.assertFalse(created)
        self.assertEqual(row["status"], "printed")
        self.assertEqual(self.fiscool.executed, 1)

    def test_acelasi_external_id_cu_alt_payment_key_e_respins(self):
        self.runner.submit_receipt(cu_cheie("e1", "p1"))
        wait_status(self.runner, "e1", ("printed",))
        with self.assertRaises(InvalidPayload):
            self.runner.submit_receipt(cu_cheie("e1", "p2"))

    def test_acelasi_payment_key_cu_alt_continut_e_respins(self):
        self.runner.submit_receipt(cu_cheie("e1", "p1"))
        wait_status(self.runner, "e1", ("printed",))
        with self.assertRaises(InvalidPayload) as ctx:
            self.runner.submit_receipt(cu_cheie("e2", "p1", qty=2))
        self.assertIn("payment_key", str(ctx.exception))
        self.assertIsNone(self.runner.store.get("e2"))   # nu rămâne niciun rând
        self.assertEqual(self.fiscool.executed, 1)

    def test_fara_payment_key_comportamentul_ramane_cel_vechi(self):
        self.runner.submit_receipt(cu_cheie("e1", None))
        wait_status(self.runner, "e1", ("printed",))
        self.runner.submit_receipt(cu_cheie("e2", None))
        wait_status(self.runner, "e2", ("printed",))
        self.assertEqual(self.fiscool.executed, 2)

    def test_payment_key_invalid_e_respins_inainte_de_orice(self):
        for cheie in ("", "a b", "x" * 65, 5):
            with self.assertRaises(InvalidPayload, msg=repr(cheie)):
                self.runner.submit_receipt(cu_cheie("e1", cheie))
        self.assertEqual(self.fiscool.requests, [])

    def test_oglinda_cat_timp_primul_job_e_incert_nu_trimite_nimic_nou(self):
        self.fiscool.mode = "drop_after"   # FisCool execută, dar răspunsul se pierde: primul job devine incert
        self.runner.submit_receipt(cu_cheie("e1", "p1"))
        wait_status(self.runner, "e1", ("uncertain",))
        row, _ = self.runner.submit_receipt(cu_cheie("e2", "p1"))
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(self.fiscool.executed, 1)
        # se lămurește pe primul job (aceeași cheie de idempotență), iar oglinda urmează
        resolve_now(self.runner, "e1")
        row = self.runner.get("e2")
        self.assertEqual(row["status"], "printed")
        self.assertEqual(self.fiscool.executed, 1)

    def test_oglinda_dupa_esec_sigur_arata_esecul_iar_reincercarea_merge_pe_primul_job(self):
        self.fiscool.error = (400, 400, "Eroare Configurare: cotă inactivă")
        self.runner.submit_receipt(cu_cheie("e1", "p1"))
        wait_status(self.runner, "e1", ("failed",))
        row, _ = self.runner.submit_receipt(cu_cheie("e2", "p1"))
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["error"]["retry_safe"])
        self.fiscool.error = None
        row, problema = self.runner.retry("e2", False)
        self.assertIsNone(problema)
        self.assertEqual(row["external_id"], "e2")
        wait_status(self.runner, "e1", ("printed",))
        self.assertEqual(self.runner.get("e2")["status"], "printed")
        self.assertEqual(self.runner.get("e1")["attempt"], 2)
        self.assertEqual(self.fiscool.executed, 1)

    def test_reincercarea_pe_oglinda_unui_job_tiparit_e_respinsa(self):
        self.runner.submit_receipt(cu_cheie("e1", "p1"))
        wait_status(self.runner, "e1", ("printed",))
        self.runner.submit_receipt(cu_cheie("e2", "p1"))
        row, problema = self.runner.retry("e2", True)
        self.assertEqual(problema, "NOT_FAILED")
        self.assertEqual(self.fiscool.executed, 1)

    def test_zece_ferestre_in_paralel_cu_acelasi_payment_key_tiparesc_un_singur_bon(self):
        erori = []

        def trimite(i):
            try:
                self.runner.submit_receipt(cu_cheie(f"e{i}", "p1"))
            except Exception as e:   # pragma: no cover - ar fi un bug
                erori.append(e)

        fire = [threading.Thread(target=trimite, args=(i,)) for i in range(10)]
        [f.start() for f in fire]
        [f.join() for f in fire]
        self.assertEqual(erori, [])
        primare = [i for i in range(10) if self.runner.store.get(f"e{i}")["status"] != "alias"]
        self.assertEqual(len(primare), 1)
        wait_status(self.runner, f"e{primare[0]}", ("printed",))
        self.assertEqual(self.fiscool.executed, 1)
        sloturi = {self.runner.get(f"e{i}")["result"]["slip_number"] for i in range(10)}
        self.assertEqual(len(sloturi), 1)

    def test_plati_diferite_cu_payment_key_diferit_tiparesc_fiecare(self):
        self.runner.submit_receipt(cu_cheie("e1", "p1"))
        self.runner.submit_receipt(cu_cheie("e2", "p2"))
        wait_status(self.runner, "e1", ("printed",))
        wait_status(self.runner, "e2", ("printed",))
        self.assertEqual(self.fiscool.executed, 2)

    def test_dupa_repornirea_agentului_oglinda_si_primul_job_se_regasesc(self):
        with tempfile.TemporaryDirectory() as d:
            cale = os.path.join(d, "jobs.sqlite3")
            runner = Runner(fiscool_cfg(self.fiscool.url), Store(cale))
            runner.submit_receipt(cu_cheie("e1", "p1"))
            wait_status(runner, "e1", ("printed",))
            runner.submit_receipt(cu_cheie("e2", "p1"))
            runner.store.close()
            runner2 = Runner(fiscool_cfg(self.fiscool.url), Store(cale))
            runner2.recover()
            self.assertEqual(runner2.get("e2")["status"], "printed")
            row, created = runner2.submit_receipt(cu_cheie("e3", "p1"))
            self.assertEqual((created, row["status"]), (False, "printed"))
            self.assertEqual(self.fiscool.executed, 1)

    def test_recover_nu_atinge_oglinzile(self):
        self.fiscool.mode = "drop_after"
        self.runner.submit_receipt(cu_cheie("e1", "p1"))
        wait_status(self.runner, "e1", ("uncertain",))
        self.runner.submit_receipt(cu_cheie("e2", "p1"))
        self.runner.recover()
        self.assertEqual(self.runner.store.get("e2")["status"], "alias")


class PaymentKeyFiscoTest(unittest.TestCase):
    """Fisco nu are idempotență: protecția la bon dublu e doar în agent."""

    def test_job_nou_cu_acelasi_payment_key_nu_mai_trimite_nimic_la_fisco(self):
        fisco = FakeFisco().start()
        self.addCleanup(fisco.stop)
        runner = make_runner(fisco_cfg(fisco.url))
        runner.submit_receipt(cu_cheie("e1", "p1"))
        wait_status(runner, "e1", ("printed",))
        trimise = len(fisco.posts)
        row, created = runner.submit_receipt(cu_cheie("e2", "p1"))
        self.assertEqual((created, row["status"]), (False, "printed"))
        self.assertEqual(len(fisco.posts), trimise)
        self.assertEqual(fisco.print_count, 1)


class StoreMigrareTest(unittest.TestCase):
    def test_o_baza_veche_fara_payment_key_se_deschide_si_primeste_coloana(self):
        with tempfile.TemporaryDirectory() as d:
            cale = os.path.join(d, "jobs.sqlite3")
            db = sqlite3.connect(cale)
            db.execute("""CREATE TABLE jobs (external_id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1, payload TEXT NOT NULL, fiscal_job_id TEXT, state TEXT NOT NULL DEFAULT '{}',
                pos TEXT, result TEXT, error TEXT, last_resolve REAL NOT NULL DEFAULT 0, started_at REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
            db.execute("INSERT INTO jobs (external_id, kind, status, payload, created_at, updated_at) VALUES ('v1','receipt','printed','{}',1,1)")
            db.commit()
            db.close()
            store = Store(cale)
            self.assertEqual(store.get("v1")["status"], "printed")
            self.assertIsNone(store.get("v1")["payment_key"])
            self.assertIsNone(store.primary_for_payment_key("x", "y"))


if __name__ == "__main__":
    unittest.main()
