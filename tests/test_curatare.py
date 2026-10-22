"""Curățarea joburilor vechi din baza agentului: doar cele terminate, doar peste termen, și niciodată un job de care depinde altul."""
import time
import unittest

from worldbit_fiscal import config as config_mod
from worldbit_fiscal.state import Store

ZI = 86400


def store_cu(*jobs):
    """jobs: (external_id, status, vechime în zile, state)"""
    s = Store(":memory:")
    for ext, status, zile, state in jobs:
        s.create(ext, "receipt", "{}", status=status, state=state)
        s._db.execute("UPDATE jobs SET updated_at = ? WHERE external_id = ?", (time.time() - zile * ZI, ext))
    s._db.commit()
    return s


def existente(s):
    return sorted(r["external_id"] for r in s._db.execute("SELECT external_id FROM jobs"))


class PurgeTest(unittest.TestCase):
    def test_deletes_only_finished_jobs_older_than_the_limit(self):
        s = store_cu(("tiparit-vechi", "printed", 10, None), ("esuat-vechi", "failed", 8, None), ("oglinda-veche", "alias", 9, {"alias_of": "x"}),
                     ("tiparit-nou", "printed", 2, None), ("esuat-nou", "failed", 0, None))
        self.assertEqual(s.purge(7 * ZI), 3)
        self.assertEqual(existente(s), ["esuat-nou", "tiparit-nou"])

    def test_never_deletes_a_job_in_progress_or_uncertain_however_old(self):
        s = store_cu(("primit", "received", 90, None), ("in-lucru", "running", 90, None), ("incert", "uncertain", 90, None))
        self.assertEqual(s.purge(7 * ZI), 0)
        self.assertEqual(existente(s), ["in-lucru", "incert", "primit"])

    def test_keeps_an_old_job_that_a_recent_mirror_points_to(self):
        s = store_cu(("primar", "failed", 30, None), ("oglinda-noua", "alias", 1, {"alias_of": "primar"}), ("alt-vechi", "printed", 30, None))
        self.assertEqual(s.purge(7 * ZI), 1)
        self.assertEqual(existente(s), ["oglinda-noua", "primar"])

    def test_an_old_mirror_does_not_protect_the_primary(self):
        s = store_cu(("primar", "failed", 30, None), ("oglinda-veche", "alias", 20, {"alias_of": "primar"}))
        self.assertEqual(s.purge(7 * ZI), 2)
        self.assertEqual(existente(s), [])

    def test_empty_database_and_large_batches(self):
        s = store_cu()
        self.assertEqual(s.purge(7 * ZI), 0)
        s = store_cu(*[(f"j{i}", "printed", 30, None) for i in range(1200)], ("nou", "printed", 0, None))
        self.assertEqual(s.purge(7 * ZI), 1200)
        self.assertEqual(existente(s), ["nou"])

    def test_a_negative_or_broken_retention_never_deletes_recent_jobs(self):
        import logging, threading
        from unittest import mock
        from worldbit_fiscal import __main__ as main
        for valoare in (-5, 0, "abc", None):
            s = store_cu(("nou", "printed", 0, None), ("vechi", "printed", 30, None))
            with mock.patch.object(threading, "Timer"):
                main._porneste_curatarea({"retention_days": valoare}, s)
            self.assertEqual(existente(s), ["nou"], valoare)

    def test_one_week_is_the_default(self):
        self.assertEqual(config_mod.DEFAULTS["retention_days"], 7)


if __name__ == "__main__":
    unittest.main()
