"""Sertarul de bani: comanda pe portul COM (aici, un fișier în loc de port), deschiderea automată la încasările cu numerar și endpointul pentru buton."""
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import urlencode

from worldbit_fiscal import auth, drawer, setari
from worldbit_fiscal.drivers import DRIVERS
from worldbit_fiscal.server import App, make_server
from tests.fakes import FakeFiscool, fiscool_cfg, make_runner, receipt_json, wait_status

ORIGIN = "https://dressbit.test"


def citeste(cale):
    with open(cale, "rb") as f:
        return f.read()


def cfg_sertar(url, port, **extra):
    cfg = fiscool_cfg(url)
    cfg["drawer"] = {"port": port, "command": "1", "auto_open": True, **extra}
    return cfg


class DrawerModulTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.fisier = os.path.join(self.dir, "COM3.txt")

    def test_scrie_comanda_cu_sfarsit_de_linie_ca_echo(self):
        ok, cod, mesaj = drawer.open_drawer({"drawer": {"port": self.fisier, "command": "1"}})
        self.assertEqual((ok, cod, mesaj), (True, None, None))
        self.assertEqual(citeste(self.fisier), b"1\r\n")

    def test_comanda_se_poate_schimba(self):
        drawer.open_drawer({"drawer": {"port": self.fisier, "command": "OPEN"}})
        self.assertEqual(citeste(self.fisier), b"OPEN\r\n")

    def test_fara_port_nu_e_configurat(self):
        for cfg in ({}, {"drawer": {"port": ""}}, {"drawer": {"port": "   "}}):
            self.assertFalse(drawer.configured(cfg))
            ok, cod, _ = drawer.open_drawer(cfg)
            self.assertEqual((ok, cod), (False, "DRAWER_NOT_CONFIGURED"))

    def test_port_inexistent_da_mesaj_clar(self):
        ok, cod, mesaj = drawer.open_drawer({"drawer": {"port": os.path.join(self.dir, "nu-exista", "COM9")}})
        self.assertEqual((ok, cod), (False, "DRAWER_FAILED"))
        self.assertIn("nu există", mesaj)

    def test_portul_blocat_nu_agata_agentul(self):
        def blocat(path, data):
            time.sleep(5)
        with mock.patch.object(drawer, "_write", blocat), mock.patch.object(drawer, "TIMEOUT_S", 0.2):
            inceput = time.time()
            ok, cod, _ = drawer.open_drawer({"drawer": {"port": "COM3"}})
        self.assertEqual((ok, cod), (False, "DRAWER_TIMEOUT"))
        self.assertLess(time.time() - inceput, 2)

    def test_numele_portului_com_primeste_prefixul_windows(self):
        self.assertEqual(drawer.device_path("COM3"), "\\\\.\\COM3")
        self.assertEqual(drawer.device_path(" com12 "), "\\\\.\\COM12")   # și COM10+, scris cu litere mici
        self.assertEqual(drawer.device_path("/dev/ttyUSB0"), "/dev/ttyUSB0")

    def test_validarea_setarilor(self):
        self.assertEqual(drawer.valideaza(" COM3 ", " 1 "), ("COM3", "1"))
        self.assertEqual(drawer.valideaza("", "1"), ("", "1"))
        for port, comanda in (("COM3", ""), ("COM3", "x" * 21), ("COM3", "a\x00b"), ("CO\nM3", "1")):
            with self.subTest((port, comanda)), self.assertRaises(ValueError):
                drawer.valideaza(port, comanda)


class DeschidereAutomataTest(unittest.TestCase):
    def setUp(self):
        self.fiscool = FakeFiscool().start()
        self.addCleanup(self.fiscool.stop)
        self.dir = tempfile.mkdtemp()
        self.fisier = os.path.join(self.dir, "COM3.txt")
        self.deschideri = []
        self.real = drawer._write

        def inregistreaza(path, data):
            self.deschideri.append(data)
            self.real(path, data)
        patcher = mock.patch.object(drawer, "_write", inregistreaza)
        patcher.start()
        self.addCleanup(patcher.stop)

    def runner(self, **extra):
        return make_runner(cfg_sertar(self.fiscool.url, self.fisier, **extra))

    def tipareste(self, runner, **kw):
        runner.submit_receipt(receipt_json(**kw))
        return wait_status(runner, "r1", ("printed",))

    def test_numerar_deschide_sertarul_dupa_tiparire(self):
        runner = self.runner()
        self.tipareste(runner)
        self.assertEqual(len(self.deschideri), 1)
        self.assertEqual(runner.get("r1")["state"]["drawer"], {"auto": True, "opened": True, "error": None})

    def test_plata_mixta_cu_numerar_deschide_sertarul(self):
        runner = self.runner()
        self.tipareste(runner, card=40)
        self.assertEqual(len(self.deschideri), 1)

    def test_doar_card_nu_deschide_sertarul(self):
        runner = self.runner()
        self.tipareste(runner, card=100, cash=0)
        self.assertEqual(self.deschideri, [])
        self.assertIsNone(runner.get("r1")["state"].get("drawer"))

    def test_dezactivat_sau_nesetat_nu_deschide(self):
        self.tipareste(self.runner(auto_open=False))
        self.assertEqual(self.deschideri, [])
        runner = make_runner(fiscool_cfg(self.fiscool.url))   # fără secțiunea sertar
        runner.submit_receipt(receipt_json("r2"))
        wait_status(runner, "r2", ("printed",))
        self.assertEqual(self.deschideri, [])

    def test_o_singura_deschidere_pe_job_chiar_daca_e_citit_de_mai_multe_ori(self):
        runner = self.runner()
        self.tipareste(runner)
        for _ in range(3):
            runner.get("r1")
            runner.submit_receipt(receipt_json())   # retrimitere identică
        self.assertEqual(len(self.deschideri), 1)

    def test_jobul_oglinda_nu_redeschide_sertarul(self):
        runner = self.runner()
        corp = json.loads(receipt_json("e1"))
        corp["payment_key"] = "p1"
        runner.submit_receipt(json.dumps(corp))
        wait_status(runner, "e1", ("printed",))
        corp["external_id"] = "e2"
        runner.submit_receipt(json.dumps(corp))
        self.assertEqual(len(self.deschideri), 1)

    def test_sertar_defect_nu_strica_bonul_si_eroarea_se_vede_in_job(self):
        runner = make_runner(cfg_sertar(self.fiscool.url, os.path.join(self.dir, "nu-exista", "COM9")))
        row = self.tipareste(runner)
        self.assertEqual(row["status"], "printed")
        self.assertEqual(runner.get("r1")["state"]["drawer"]["opened"], False)
        self.assertEqual(runner.get("r1")["state"]["drawer"]["error"]["code"], "DRAWER_FAILED")

    def test_bonul_esuat_nu_deschide_sertarul(self):
        self.fiscool.error = (400, 400, "Eroare Configurare: cotă inactivă")
        runner = self.runner()
        runner.submit_receipt(receipt_json())
        wait_status(runner, "r1", ("failed",))
        self.assertEqual(self.deschideri, [])

    def test_bonul_lamurit_dupa_incertitudine_deschide_o_data(self):
        self.fiscool.mode = "drop_after"   # răspunsul se pierde: incert, apoi lămurit pe aceeași cheie
        runner = self.runner()
        runner.submit_receipt(receipt_json())
        wait_status(runner, "r1", ("uncertain",))
        self.assertEqual(self.deschideri, [])
        from tests.fakes import resolve_now
        resolve_now(runner, "r1")
        self.assertEqual(wait_status(runner, "r1", ("printed",))["status"], "printed")
        self.assertEqual(len(self.deschideri), 1)


class DrawerServerTest(unittest.TestCase):
    def setUp(self):
        self.fiscool = FakeFiscool().start()
        self.addCleanup(self.fiscool.stop)
        self.dir = tempfile.mkdtemp()
        self.fisier = os.path.join(self.dir, "COM3.txt")
        cfg = cfg_sertar(self.fiscool.url, self.fisier)
        cfg["allowed_origins"] = [ORIGIN]
        cfg["agent_id"] = "agent-test-1"
        cfg["admin"] = auth.hash_password("parola-admin-1")
        self.app = App(cfg, make_runner(cfg), self.dir)
        self.httpd = make_server(self.app, port=0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, method, path, body=None, origin=ORIGIN, cookie=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        if cookie:
            headers["Cookie"] = cookie
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read().decode()
        out = (resp.status, dict(resp.getheaders()), data)
        conn.close()
        return out

    def test_butonul_deschide_sertarul(self):
        status, _, data = self.call("POST", "/drawer/open")
        self.assertEqual((status, json.loads(data)), (200, {"ok": True}))
        self.assertEqual(citeste(self.fisier), b"1\r\n")

    def test_origine_straina_e_respinsa_si_nu_deschide(self):
        status, _, _ = self.call("POST", "/drawer/open", origin="https://evil.example")
        self.assertEqual(status, 403)
        self.assertFalse(os.path.exists(self.fisier))

    def test_fara_port_configurat_da_409(self):
        self.app.cfg["drawer"]["port"] = ""
        status, _, data = self.call("POST", "/drawer/open")
        self.assertEqual((status, json.loads(data)["error"]["code"]), (409, "DRAWER_NOT_CONFIGURED"))

    def test_port_defect_da_502_cu_mesaj(self):
        self.app.cfg["drawer"]["port"] = os.path.join(self.dir, "nu-exista", "COM9")
        status, _, data = self.call("POST", "/drawer/open")
        err = json.loads(data)["error"]
        self.assertEqual((status, err["code"]), (502, "DRAWER_FAILED"))
        self.assertIn("nu există", err["message"])

    def test_health_arata_daca_sertarul_e_configurat(self):
        _, _, data = self.call("GET", "/health")
        self.assertEqual(json.loads(data)["drawer"], {"configured": True})
        self.app.cfg["drawer"]["port"] = ""
        _, _, data = self.call("GET", "/health")
        self.assertEqual(json.loads(data)["drawer"], {"configured": False})

    def test_job_arata_deschiderea_automata(self):
        self.call("POST", "/print", receipt_json())
        for _ in range(300):
            _, _, data = self.call("GET", "/jobs/r1")
            job = json.loads(data)
            if job["status"] == "printed":
                break
            time.sleep(0.02)
        self.assertEqual(job["drawer"], {"auto": True, "opened": True, "error": None})

    def test_setari_salveaza_si_testeaza_sertarul(self):
        _, hdrs, _ = self.call("POST", "/setari/login", urlencode({"password": "parola-admin-1"}), origin=f"http://127.0.0.1:{self.port}")
        cookie = hdrs["Set-Cookie"].split(";")[0]
        propriu = f"http://127.0.0.1:{self.port}"
        form = {"port": "4747", "allowed_origins": ORIGIN, "pos_model": "none", "drawer_present": "1",
                "drawer_port": self.fisier, "drawer_command": "7", "drawer_auto": "on",
                "d_base_url": self.fiscool.url, "d_device_id": FakeFiscool.DEVICE, "d_vat_map": "", "d_max_name_length": "36",
                "d_request_timeout_s": "25"}
        status, _, _ = self.call("POST", "/setari/save", urlencode(form), origin=propriu, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(self.app.cfg["drawer"], {"port": self.fisier, "command": "7", "auto_open": True})
        status, _, html = self.call("POST", "/setari/drawer-test", urlencode(form), origin=propriu, cookie=cookie)
        self.assertIn("Sertarul s-a deschis", html)
        self.assertEqual(citeste(self.fisier), b"7\r\n")
        # bifa debifată = deschiderea automată oprită
        del form["drawer_auto"]
        self.call("POST", "/setari/save", urlencode(form), origin=propriu, cookie=cookie)
        self.assertFalse(self.app.cfg["drawer"]["auto_open"])
        # comandă invalidă = respinsă, setarea veche rămâne
        form["drawer_command"] = ""
        status, _, html = self.call("POST", "/setari/save", urlencode(form), origin=propriu, cookie=cookie)
        self.assertEqual(status, 400)
        self.assertEqual(self.app.cfg["drawer"]["command"], "7")

    def test_formular_fara_campurile_sertarului_nu_atinge_setarea(self):
        _, hdrs, _ = self.call("POST", "/setari/login", urlencode({"password": "parola-admin-1"}), origin=f"http://127.0.0.1:{self.port}")
        cookie = hdrs["Set-Cookie"].split(";")[0]
        form = {"port": "4747", "allowed_origins": ORIGIN, "pos_model": "none", "d_base_url": self.fiscool.url,
                "d_device_id": FakeFiscool.DEVICE, "d_vat_map": "", "d_max_name_length": "36", "d_request_timeout_s": "25"}
        self.call("POST", "/setari/save", urlencode(form), origin=f"http://127.0.0.1:{self.port}", cookie=cookie)
        self.assertEqual(self.app.cfg["drawer"]["port"], self.fisier)


if __name__ == "__main__":
    unittest.main()
