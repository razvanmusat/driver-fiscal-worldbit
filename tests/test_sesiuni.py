"""Sesiunile paginii de setări: se păstrează peste repornirea agentului, dar expiră și se pot închide."""
import json
import os
import tempfile
import time
import unittest

from worldbit_fiscal import auth


class SessionsTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "sessions.json")

    def test_a_session_survives_a_restart(self):
        token = auth.Sessions(self.path).create()
        self.assertTrue(auth.Sessions(self.path).valid(token))

    def test_a_dropped_session_does_not_come_back(self):
        s = auth.Sessions(self.path)
        token = s.create()
        s.drop(token)
        self.assertFalse(auth.Sessions(self.path).valid(token))

    def test_expired_sessions_are_not_loaded(self):
        with open(self.path, "w") as f:
            json.dump({"vechi": time.time() - 5, "bun": time.time() + 600}, f)
        s = auth.Sessions(self.path)
        self.assertFalse(s.valid("vechi"))
        self.assertTrue(s.valid("bun"))

    def test_a_missing_or_broken_file_means_no_sessions_not_a_crash(self):
        self.assertFalse(auth.Sessions(self.path).valid("x"))
        with open(self.path, "w") as f:
            f.write("nu e json")
        self.assertFalse(auth.Sessions(self.path).valid("x"))
        token = auth.Sessions(self.path).create()                 # fișierul stricat e rescris la prima sesiune nouă
        self.assertTrue(auth.Sessions(self.path).valid(token))

    def test_without_a_path_nothing_is_written(self):
        s = auth.Sessions()
        self.assertTrue(s.valid(s.create()))

    @unittest.skipUnless(os.name == "posix", "permisiuni POSIX")
    def test_the_file_is_readable_only_by_its_owner(self):
        auth.Sessions(self.path).create()
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_an_unwritable_location_does_not_break_login(self):
        s = auth.Sessions("/nu/exista/deloc/sessions.json")
        self.assertTrue(s.valid(s.create()))


if __name__ == "__main__":
    unittest.main()
