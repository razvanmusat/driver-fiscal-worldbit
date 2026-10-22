"""Butonul „Actualizează”: verificarea „are modificări pentru driverul și POS-ul de aici” și pașii de dinaintea instalării. Instalarea propriu-zisă
(task programat Windows) se verifică pe Windows, nu aici."""
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from worldbit_fiscal import updater
from worldbit_fiscal.version import __version__
from tests.fakes import fisco_cfg, fiscool_cfg, make_runner

LOCAL = updater.installed_package()


NOUA = "9.9.9"   # o versiune mai nouă decât oricare curentă: testele nu depind de versiunea agentului


def make_release(version=NOUA, edits=(), top="driver-fiscal-worldbit-main", extra=None, replace=None):
    """O arhivă ca cea de pe GitHub (dosar-rădăcină cu pachetul în el), pornind de la pachetul real, cu modificările cerute."""
    tmp = Path(tempfile.mkdtemp())
    pkg = tmp / "src" / top / "worldbit_fiscal"
    shutil.copytree(LOCAL, pkg, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (pkg / "version.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    for rel in edits:
        f = pkg / rel
        f.write_text((f.read_text(encoding="utf-8") if f.exists() else "") + "\n# modificare\n", encoding="utf-8")
    for rel, text in (replace or {}).items():
        (pkg / rel).write_text(text, encoding="utf-8")
    zip_path = tmp / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        for f in (tmp / "src").rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(tmp / "src").as_posix())
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return zip_path, pkg


def decision(release_pkg, cfg):
    return updater.decide(LOCAL, release_pkg, updater.module_files(cfg))


class DecideTest(unittest.TestCase):
    def setUp(self):
        self.cfg = fiscool_cfg("http://x")            # driver instalat: fiscool, fără POS

    def state(self, **kw):
        cfg = kw.pop("cfg", self.cfg)
        _, pkg = make_release(**kw)
        return decision(pkg, cfg)[0]

    def test_same_or_older_version_is_current(self):
        self.assertEqual(self.state(version=__version__), "current")
        self.assertEqual(self.state(version="0.9.0", edits=["runner.py"]), "current")

    def test_newer_version_without_changes_for_this_machine_is_not_installed(self):
        self.assertEqual(self.state(version="1.1.0"), "irrelevant")                                  # doar versiunea
        self.assertEqual(self.state(version="1.1.0", edits=["drivers/fisco.py"]), "irrelevant")      # alt driver fiscal
        self.assertEqual(self.state(version="1.1.0", edits=["drivers/catalog_fisco.py"]), "irrelevant")
        self.assertEqual(self.state(version="1.1.0", edits=["pos/hypercom_ecr.py"]), "irrelevant")   # POS neales aici
        self.assertEqual(self.state(version="1.1.0", edits=["drivers/driver_nou.py", "pos/pos_nou.py"]), "irrelevant")
        self.assertEqual(self.state(version="1.1.0", edits=["drivers/__init__.py", "pos/__init__.py"]), "irrelevant")   # listele de înregistrare

    def test_change_in_the_installed_driver_or_in_the_core_is_installed(self):
        self.assertEqual(self.state(version=NOUA, edits=["drivers/fiscool.py"]), "update")
        self.assertEqual(self.state(version=NOUA, edits=["drivers/catalog_fiscool.py"]), "update")
        self.assertEqual(self.state(version=NOUA, edits=["drivers/base.py"]), "update")
        self.assertEqual(self.state(version=NOUA, edits=["runner.py"]), "update")
        self.assertEqual(self.state(version=NOUA, edits=["serial_io.py"]), "update")

    def test_change_in_the_chosen_pos_model_is_installed_only_when_that_pos_is_chosen(self):
        with_pos = fiscool_cfg("http://x")
        with_pos["pos"] = {"model": "hypercom_ecr", "settings": {}}
        for rel in ("pos/hypercom_ecr.py", "pos/ecr.py"):
            with self.subTest(rel):
                self.assertEqual(self.state(version=NOUA, edits=[rel], cfg=with_pos), "update")
                self.assertEqual(self.state(version=NOUA, edits=[rel], cfg=self.cfg), "irrelevant")

    def test_the_other_driver_is_the_relevant_one_when_it_is_installed(self):
        fisco = fisco_cfg("http://x")
        self.assertEqual(self.state(version=NOUA, edits=["drivers/fisco.py"], cfg=fisco), "update")
        self.assertEqual(self.state(version=NOUA, edits=["drivers/fiscool.py"], cfg=fisco), "irrelevant")

    def test_messages_say_what_happens(self):
        _, pkg = make_release(version="1.1.0")
        self.assertIn("Nimic de actualizat", decision(pkg, self.cfg)[1])
        self.assertIn("1.1.0", decision(pkg, self.cfg)[1])
        _, pkg = make_release(version=NOUA, edits=["runner.py"])
        self.assertEqual(decision(pkg, self.cfg)[1], f"Actualizare disponibilă: {__version__} -> {NOUA}.")


class ExtractTest(unittest.TestCase):
    def test_finds_the_package_inside_the_github_style_root_folder(self):
        zip_path, _ = make_release()
        pkg = updater.extract(zip_path, tempfile.mkdtemp())
        self.assertEqual(pkg.name, "worldbit_fiscal")
        self.assertEqual(updater.read_version(pkg), (9, 9, 9))

    def test_refuses_paths_outside_the_target_folder(self):
        zip_path, _ = make_release(extra={"../evil.py": "x"})
        with self.assertRaises(updater.UpdateError):
            updater.extract(zip_path, tempfile.mkdtemp())

    def test_refuses_archives_without_the_package_or_that_are_not_zip(self):
        bad = Path(tempfile.mkdtemp()) / "a.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("alt/ceva.txt", "x")
        with self.assertRaises(updater.UpdateError):
            updater.extract(bad, tempfile.mkdtemp())
        notzip = Path(tempfile.mkdtemp()) / "b.zip"
        notzip.write_text("nu e zip")
        with self.assertRaises(updater.UpdateError):
            updater.extract(notzip, tempfile.mkdtemp())

    def test_refuses_an_archive_that_expands_to_an_absurd_size(self):
        from unittest import mock as _mock
        zip_path, _ = make_release()
        with _mock.patch.object(updater, "MAX_UNZIPPED_BYTES", 1000):
            with self.assertRaises(updater.UpdateError) as ctx:
                updater.extract(zip_path, tempfile.mkdtemp())
        self.assertIn("prea mare", str(ctx.exception))

    def test_the_backup_of_the_data_is_made_only_after_the_agent_is_stopped(self):
        script = updater._PS
        self.assertLess(script.index("    Stop-Agent\n    $backup = Backup-Data"), script.index("Rename-Item $pkg $bak"))

    def test_refuses_a_package_without_a_valid_version(self):
        zip_path, pkg = make_release()
        (pkg / "version.py").write_text('__version__ = "abc"\n')
        with self.assertRaises(updater.UpdateError):
            updater.read_version(pkg)


class RunUpdateTest(unittest.TestCase):
    """Ce face butonul, cu descărcarea dintr-un fișier local și fără instalarea propriu-zisă."""

    def setUp(self):
        self.cfg = fiscool_cfg("http://x")
        self.cfg["update_url"] = None
        self.home = tempfile.mkdtemp()
        self.runner = make_runner(self.cfg)
        self.staged = []
        for patcher in (mock.patch.object(updater, "_stage_and_launch", lambda *a: self.staged.append(a)),
                        mock.patch.object(updater, "can_install", lambda: (True, ""))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_with(self, **release):
        zip_path, _ = make_release(**release)
        self.cfg["update_url"] = zip_path.as_uri()
        return updater.run_update(self.cfg, self.runner, self.home)

    def test_same_version_says_so_and_installs_nothing(self):
        self.assertIn("Ai deja ultima versiune", self.run_with(version=__version__))
        self.assertEqual(self.staged, [])

    def test_newer_version_without_changes_for_this_machine_installs_nothing(self):
        text = self.run_with(version="1.1.0", edits=["drivers/fisco.py"])
        self.assertIn("Nimic de actualizat", text)
        self.assertEqual(self.staged, [])

    def test_relevant_change_starts_the_installation(self):
        text = self.run_with(version=NOUA, edits=["drivers/fiscool.py"])
        self.assertIn("Instalarea a pornit", text)
        self.assertEqual(len(self.staged), 1)
        _, home, version, old, port = self.staged[0]
        self.assertEqual((home, version, old, port), (self.home, NOUA, __version__, 4747))

    def test_operations_in_progress_block_the_installation(self):
        self.runner.store.create("job-in-curs", "receipt", "{}")        # primit, nefinalizat
        text = self.run_with(version=NOUA, edits=["runner.py"])
        self.assertIn("operațiuni fiscale în curs", text)
        self.assertEqual(self.staged, [])

    def test_outside_the_normal_windows_install_it_only_reports(self):
        with mock.patch.object(updater, "can_install", lambda: (False, "agentul nu rulează din instalarea normală")):
            text = self.run_with(version=NOUA, edits=["runner.py"])
        self.assertIn("Nu se poate instala de aici", text)
        self.assertEqual(self.staged, [])

    def test_unreachable_or_broken_downloads_report_without_raising(self):
        self.cfg["update_url"] = "file:///nu/exista/release.zip"
        self.assertIn("Nu s-a putut verifica actualizarea", updater.run_update(self.cfg, self.runner, self.home))
        broken = Path(tempfile.mkdtemp()) / "x.zip"
        broken.write_text("nu e zip")
        self.cfg["update_url"] = broken.as_uri()
        self.assertIn("Nu s-a putut verifica actualizarea", updater.run_update(self.cfg, self.runner, self.home))
        self.assertEqual(self.staged, [])

    def test_a_failure_to_start_the_installation_is_reported(self):
        def boom(*a):
            raise updater.UpdateError("schtasks a eșuat")
        with mock.patch.object(updater, "_stage_and_launch", boom):
            text = self.run_with(version=NOUA, edits=["runner.py"])
        self.assertIn("Nu s-a putut porni instalarea: schtasks a eșuat", text)

    def test_the_default_source_is_the_public_github_repository(self):
        self.assertTrue(updater.DEFAULT_URL.startswith("https://github.com/razvanmusat/driver-fiscal-worldbit/"))


class PreflightTest(unittest.TestCase):
    """Pachetul nou se verifică ÎNAINTE de orice oprire a agentului, iar motivul se spune exact."""

    def pkg(self, **kw):
        return make_release(**kw)[1]

    def test_a_good_package_passes(self):
        updater.preflight(self.pkg(version=NOUA), NOUA)

    def test_a_syntax_error_is_reported_with_the_file_and_the_kind_of_error(self):
        with self.assertRaises(updater.UpdateError) as ctx:
            updater.preflight(self.pkg(version="1.0.2", replace={"runner.py": "def broken(:\n"}), "1.0.2")
        self.assertIn("nu poate porni", str(ctx.exception))
        self.assertIn("SyntaxError", str(ctx.exception))
        self.assertIn("runner.py", ctx.exception.detail)

    def test_an_import_error_is_reported(self):
        with self.assertRaises(updater.UpdateError) as ctx:
            updater.preflight(self.pkg(version="1.0.2", edits=["server.py"], replace={"setari.py": "import modul_care_nu_exista\n"}), "1.0.2")
        self.assertIn("modul_care_nu_exista", str(ctx.exception))

    def test_a_package_that_declares_another_version_is_refused(self):
        pkg = self.pkg(version="1.0.2")
        with self.assertRaises(updater.UpdateError) as ctx:
            updater.preflight(pkg, "1.0.9")
        self.assertIn("declară versiunea 1.0.2", str(ctx.exception))

    def test_the_service_python_without_console_is_replaced_by_the_console_one(self):
        with mock.patch.object(updater.sys, "executable", r"C:\\Python312\\pythonw.exe"), mock.patch.object(updater.os.path, "exists", lambda p: True):
            self.assertTrue(updater.python_exe().lower().endswith("python.exe"))
        with mock.patch.object(updater.sys, "executable", "/usr/bin/python3"):
            self.assertEqual(updater.python_exe(), "/usr/bin/python3")


class RunUpdateInfoTest(unittest.TestCase):
    """Rezultatul structurat, pe care îl folosește fereastra de progres din pagină."""

    def setUp(self):
        self.cfg = fiscool_cfg("http://x")
        self.home = tempfile.mkdtemp()
        self.runner = make_runner(self.cfg)
        self.staged = []
        for patcher in (mock.patch.object(updater, "_stage_and_launch", lambda *a: self.staged.append(a)),
                        mock.patch.object(updater, "can_install", lambda: (True, ""))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def info(self, **release):
        zip_path, _ = make_release(**release)
        self.cfg["update_url"] = zip_path.as_uri()
        return updater.run_update_info(self.cfg, self.runner, self.home)

    def test_every_outcome_has_a_state_a_message_and_the_target_version_only_when_installing(self):
        r = self.info(version=__version__)
        self.assertEqual((r["state"], r["target"]), ("current", None))
        r = self.info(version="1.1.0", edits=["drivers/fisco.py"])
        self.assertEqual((r["state"], r["target"]), ("irrelevant", None))
        r = self.info(version=NOUA, edits=["drivers/fiscool.py"])
        self.assertEqual((r["state"], r["target"]), ("started", NOUA))
        self.assertIn("Instalarea a pornit", r["message"])
        self.runner.store.create("in-curs", "receipt", "{}")
        r = self.info(version="1.0.2", edits=["runner.py"])
        self.assertEqual((r["state"], r["target"]), ("busy", None))

    def test_unavailable_and_error_states(self):
        with mock.patch.object(updater, "can_install", lambda: (False, "doar pe Windows")):
            r = self.info(version=NOUA, edits=["runner.py"])
        self.assertEqual(r["state"], "unavailable")
        self.cfg["update_url"] = "file:///nu/exista.zip"
        self.assertEqual(updater.run_update_info(self.cfg, self.runner, self.home)["state"], "error")

    def test_a_broken_release_is_refused_with_the_reason_and_never_touches_the_running_agent(self):
        r = self.info(version="1.0.2", replace={"runner.py": "def broken(:\n"})
        self.assertEqual(r["state"], "error")
        self.assertIn("Versiunea 1.0.2 nu a fost instalată", r["message"])
        self.assertIn("SyntaxError", r["message"])
        self.assertIn("Agentul actual nu a fost atins", r["message"])
        self.assertIn("runner.py", r["detail"])
        self.assertEqual(self.staged, [])                     # nu s-a pregătit nicio instalare, deci agentul n-a fost oprit

    def test_the_text_version_returns_the_same_message(self):
        zip_path, _ = make_release(version=__version__)
        self.cfg["update_url"] = zip_path.as_uri()
        self.assertEqual(updater.run_update(self.cfg, self.runner, self.home), updater.run_update_info(self.cfg, self.runner, self.home)["message"])


class UpdateResultTest(unittest.TestCase):
    def test_reads_the_structured_result_written_by_the_install_script(self):
        home = Path(tempfile.mkdtemp())
        self.assertIsNone(updater.update_result(home))
        (home / "update").mkdir()
        (home / "update" / "result.json").write_text('{"ok": false, "from": "1.0.0", "to": "1.0.2", "reason": "RuntimeError: x"}', encoding="utf-8-sig")
        self.assertEqual(updater.update_result(home)["reason"], "RuntimeError: x")
        (home / "update" / "result.json").write_text("nu e json", encoding="utf-8")
        self.assertIsNone(updater.update_result(home))


class LastLogTest(unittest.TestCase):
    def test_reads_the_result_of_the_last_update(self):
        home = Path(tempfile.mkdtemp())
        self.assertIsNone(updater.last_log(home))
        (home / "update").mkdir()
        (home / "update" / "last.txt").write_text("2026-10-21T20:00:00 OK: actualizat\n", encoding="utf-8-sig")
        self.assertIn("OK: actualizat", updater.last_log(home))


if __name__ == "__main__":
    unittest.main()
