import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class InitOutputTest(unittest.TestCase):
    def test_init_works_when_the_output_cannot_encode_romanian_letters(self):
        """Instalatorul Windows rulează `init` cu ieșirea redirecționată (cp1252): mesajele cu „ț”/„ă” nu au voie să oprească programul."""
        home = tempfile.mkdtemp()
        env = {**os.environ, "PYTHONIOENCODING": "cp1252", "WORLDBIT_FISCAL_HOME": home, "WORLDBIT_ADMIN_PASSWORD": "parola-test-1"}
        r = subprocess.run([sys.executable, "-m", "worldbit_fiscal", "init", "--driver", "fiscool"], env=env, capture_output=True,
                           cwd=Path(__file__).resolve().parent.parent)
        self.assertEqual(r.returncode, 0, r.stderr.decode("cp1252", "replace"))
        self.assertTrue((Path(home) / "config.json").exists())

    def init(self, home, *args):
        env = {**os.environ, "WORLDBIT_FISCAL_HOME": home, "WORLDBIT_ADMIN_PASSWORD": "parola-test-1"}
        return subprocess.run([sys.executable, "-m", "worldbit_fiscal", "init", "--driver", "fiscool", *args], env=env, capture_output=True,
                              cwd=Path(__file__).resolve().parent.parent)

    def test_init_cu_pos_il_scrie_in_configuratie_si_reinstalarea_cu_acelasi_pos_pastreaza_setarile(self):
        home = tempfile.mkdtemp()
        self.assertEqual(self.init(home, "--pos", "hypercom_ecr").returncode, 0)
        cfg_path = Path(home) / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(cfg["pos"]["model"], "hypercom_ecr")
        cfg["pos"]["settings"] = {"port": "COM5"}
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertEqual(self.init(home, "--pos", "hypercom_ecr").returncode, 0)
        self.assertEqual(json.loads(cfg_path.read_text(encoding="utf-8"))["pos"]["settings"], {"port": "COM5"})
        self.assertEqual(self.init(home, "--pos", "none").returncode, 0)
        self.assertEqual(json.loads(cfg_path.read_text(encoding="utf-8"))["pos"], {"model": "none", "settings": {}})
        self.assertEqual(self.init(home, "--pos", "inexistent").returncode, 2)

    def test_init_fara_pos_nu_atinge_setarile_POS_existente(self):
        home = tempfile.mkdtemp()
        self.init(home, "--pos", "hypercom_ecr")
        self.assertEqual(self.init(home).returncode, 0)
        self.assertEqual(json.loads((Path(home) / "config.json").read_text(encoding="utf-8"))["pos"]["model"], "hypercom_ecr")


if __name__ == "__main__":
    unittest.main()
