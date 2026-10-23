import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode

from worldbit_fiscal import auth, config as config_mod
from worldbit_fiscal.server import App, make_server
from tests.fakes import FakeFisco, fisco_cfg, make_runner, receipt_json

ORIGIN = "https://dressbit.test"


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.fisco = FakeFisco().start()
        self.addCleanup(self.fisco.stop)
        self.home = tempfile.mkdtemp()
        cfg = fisco_cfg(self.fisco.url)
        cfg["allowed_origins"] = [ORIGIN]
        cfg["agent_id"] = "agent-test-1"
        cfg["admin"] = auth.hash_password("parola-admin-1")
        self.runner = make_runner(cfg)
        self.app = App(cfg, self.runner, self.home)
        self.httpd = make_server(self.app, port=0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read().decode()
        conn.close()
        return resp.status, dict(resp.getheaders()), data

    def api(self, method, path, body=None, origin=ORIGIN, extra=None):
        headers = {"Content-Type": "application/json", **(extra or {})}
        if origin:
            headers["Origin"] = origin
        status, hdrs, data = self.call(method, path, body, headers)
        return status, hdrs, (json.loads(data) if data else None)

    def wait_printed(self, external_id):
        for _ in range(300):
            status, _, data = self.api("GET", f"/jobs/{external_id}")
            if data["status"] in ("printed", "failed", "uncertain"):
                return data
            time.sleep(0.02)
        self.fail("job nefinalizat")

    # ------------------------------------------------------------------ API
    def test_print_flow_and_cors(self):
        status, hdrs, data = self.api("POST", "/print", receipt_json(card=40))
        self.assertEqual(status, 202)
        self.assertEqual(hdrs["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(data["external_id"], "r1")
        job = self.wait_printed("r1")
        self.assertEqual(job["status"], "printed")
        self.assertEqual(job["result"]["slip_number"], "6001")
        self.assertEqual(job["driver"], "fisco")
        status, _, again = self.api("POST", "/print", receipt_json(card=40))
        self.assertEqual(status, 200)
        self.assertEqual(self.fisco.print_count, 1)

    def test_same_external_id_different_content_conflicts(self):
        self.api("POST", "/print", receipt_json())
        status, _, data = self.api("POST", "/print", receipt_json(qty=2))
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "INVALID_PAYLOAD")

    def test_invalid_payload_400_and_nothing_sent(self):
        bad = json.loads(receipt_json())
        bad["total"] = "1.00"
        status, _, data = self.api("POST", "/print", json.dumps(bad))
        self.assertEqual(status, 400)
        self.assertEqual(self.fisco.posts, [])
        status, _, _ = self.api("POST", "/print", "nu e json")
        self.assertEqual(status, 400)

    def test_preflight(self):
        status, hdrs, _ = self.call("OPTIONS", "/print", headers={
            "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"})
        self.assertEqual(status, 204)
        self.assertEqual(hdrs["Access-Control-Allow-Origin"], ORIGIN)
        self.assertIn("POST", hdrs["Access-Control-Allow-Methods"])
        self.assertNotIn("Access-Control-Allow-Private-Network", hdrs)

    def test_foreign_origin_rejected(self):
        status, hdrs, _ = self.api("POST", "/print", receipt_json(), origin="https://evil.test")
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", hdrs)
        self.assertEqual(self.fisco.posts, [])
        status, _, _ = self.call("OPTIONS", "/print", headers={"Origin": "https://evil.test"})
        self.assertEqual(status, 403)

    def test_wrong_host_rejected(self):
        status, _, _ = self.call("GET", "/health", headers={"Host": "evil.test"})
        self.assertEqual(status, 403)

    def test_agent_identity_is_in_health_and_in_every_job(self):
        _, _, health = self.api("GET", "/health")
        self.assertEqual(health["agent_id"], "agent-test-1")
        _, _, created = self.api("POST", "/print", receipt_json())
        self.assertEqual(created["agent_id"], "agent-test-1")
        self.assertEqual(self.wait_printed("r1")["agent_id"], "agent-test-1")

    def test_agent_identity_survives_saving_settings(self):
        cookie = self.login()
        form = {"port": "4747", "allowed_origins": ORIGIN, "pos_model": "none", "d_base_url": self.fisco.url, "d_vat_map": "",
                "d_max_name_length": "72", "d_poll_timeout_s": "30", "d_poll_interval_s": "1"}
        status, _, _ = self.setari_post("/setari/save", form, cookie)
        self.assertEqual(status, 200)
        self.assertEqual(config_mod.load(self.home)["agent_id"], "agent-test-1")
        _, _, health = self.api("GET", "/health")
        self.assertEqual(health["agent_id"], "agent-test-1")

    def test_every_response_announces_connection_close(self):
        _, hdrs, _ = self.api("GET", "/health")
        self.assertEqual(hdrs["Connection"], "close")
        status, hdrs, _ = self.call("OPTIONS", "/print", headers={"Origin": ORIGIN})
        self.assertEqual((status, hdrs["Connection"]), (204, "close"))
        status, hdrs, _ = self.call("GET", "/nu-exista")
        self.assertEqual(hdrs["Connection"], "close")

    def test_preflight_answers_the_private_network_request_only_for_allowed_origins(self):
        pna = {"Origin": ORIGIN, "Access-Control-Request-Method": "POST", "Access-Control-Request-Private-Network": "true"}
        status, hdrs, _ = self.call("OPTIONS", "/print", headers=pna)
        self.assertEqual(status, 204)
        self.assertEqual(hdrs["Access-Control-Allow-Private-Network"], "true")
        self.assertEqual(hdrs["Access-Control-Allow-Origin"], ORIGIN)
        status, hdrs, _ = self.call("OPTIONS", "/print", headers={k: v for k, v in pna.items() if k != "Access-Control-Request-Private-Network"})
        self.assertEqual(status, 204)
        self.assertNotIn("Access-Control-Allow-Private-Network", hdrs)             # nu se anunță când nu e cerut
        status, hdrs, _ = self.call("OPTIONS", "/print", headers={**pna, "Origin": "https://strain.test"})
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Private-Network", hdrs)             # o origine nepermisă nu primește nimic

    def test_health(self):
        status, _, data = self.api("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["fiscal"]["ready"])
        self.assertEqual(data["pos"], {"model": "none", "configured": False})
        self.fisco.ready = False
        _, _, data = self.api("GET", "/health")
        self.assertFalse(data["fiscal"]["ready"])

    def test_unknown_job_404_and_reports(self):
        status, _, _ = self.api("GET", "/jobs/nu-exista")
        self.assertEqual(status, 404)
        status, _, _ = self.api("POST", "/reports", json.dumps({"external_id": "z1", "type": "Z"}))
        self.assertEqual(status, 202)
        self.assertEqual(self.wait_printed("z1")["result"]["report_number"], "431")
        status, _, _ = self.api("POST", "/reports", json.dumps({"external_id": "z2", "type": "Q"}))
        self.assertEqual(status, 400)

    def test_retry_endpoint(self):
        self.fisco.mode = "reject"
        self.api("POST", "/print", receipt_json())
        self.assertEqual(self.wait_printed("r1")["status"], "failed")
        self.fisco.mode = "normal"
        status, _, _ = self.api("POST", "/jobs/r1/retry", "{}")
        self.assertEqual(status, 202)
        self.assertEqual(self.wait_printed("r1")["status"], "printed")
        status, _, data = self.api("POST", "/jobs/r1/retry", "{}")
        self.assertEqual(status, 409)

    # ------------------------------------------------------------------ /setari
    def setari_post(self, path, form, cookie=None, origin=None):
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if cookie:
            headers["Cookie"] = cookie
        if origin:
            headers["Origin"] = origin
        return self.call("POST", path, urlencode(form), headers)

    def login(self):
        status, hdrs, _ = self.setari_post("/setari/login", {"password": "parola-admin-1"})
        self.assertEqual(status, 303)
        return hdrs["Set-Cookie"].split(";")[0]

    def test_setari_requires_login(self):
        status, _, page = self.call("GET", "/setari")
        self.assertEqual(status, 200)
        self.assertIn("Autentificare", page)
        self.assertNotIn("Adresa Fisco", page)
        status, _, _ = self.setari_post("/setari/save", {"port": "4747"})
        self.assertEqual(status, 401)

    def test_setari_wrong_password_and_lockout(self):
        for _ in range(5):
            status, _, _ = self.setari_post("/setari/login", {"password": "gresita"})
            self.assertEqual(status, 401)
        status, _, _ = self.setari_post("/setari/login", {"password": "parola-admin-1"})
        self.assertEqual(status, 429)

    def test_setari_shows_only_selected_driver_fields_and_saves(self):
        cookie = self.login()
        status, _, page = self.call("GET", "/setari", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn("Adresa Fisco", page)
        self.assertNotIn("FisCool", page)
        form = {"port": "4747", "allowed_origins": f"{ORIGIN}\nhttps://alta.test", "pos_model": "none",
                "d_base_url": self.fisco.url, "d_vat_map": "21=1\n9=2", "d_max_name_length": "40",
                "d_poll_timeout_s": "20", "d_poll_interval_s": "0.5"}
        status, _, page = self.setari_post("/setari/save", form, cookie)
        self.assertEqual(status, 200, page)
        self.assertIn("Setările au fost salvate", page)
        saved = config_mod.load(self.home)
        self.assertEqual(saved["drivers"]["fisco"]["vat_map"], {"21": "1", "9": "2"})
        self.assertEqual(saved["drivers"]["fisco"]["max_name_length"], 40)
        self.assertEqual(saved["allowed_origins"], [ORIGIN, "https://alta.test"])
        self.assertEqual(self.runner.fiscal.settings["max_name_length"], 40)   # aplicat fără repornire

    def test_setari_validation_errors(self):
        cookie = self.login()
        base = {"port": "4747", "allowed_origins": "", "pos_model": "none", "d_base_url": self.fisco.url,
                "d_vat_map": "", "d_max_name_length": "72", "d_poll_timeout_s": "30", "d_poll_interval_s": "1"}
        for label, change in {"port": {"port": "80"}, "origin": {"allowed_origins": "dressbit.ro/cale"},
                              "map": {"d_vat_map": "21"}, "int": {"d_max_name_length": "abc"},
                              "pos": {"pos_model": "necunoscut"}}.items():
            status, _, _ = self.setari_post("/setari/save", {**base, **change}, cookie)
            with self.subTest(label):
                self.assertEqual(status, 400)

    def test_setari_rejects_foreign_origin_and_api_origin(self):
        cookie = self.login()
        for origin in ("https://evil.test", ORIGIN):
            status, _, _ = self.setari_post("/setari/save", {"port": "4747"}, cookie, origin)
            with self.subTest(origin):
                self.assertEqual(status, 403)

    def test_update_button_reports_without_installing_when_nothing_changed(self):
        from tests.test_update import make_release
        from worldbit_fiscal.version import __version__
        cookie = self.login()
        _, _, page = self.call("GET", "/setari", headers={"Cookie": cookie})
        self.assertIn(f"Versiune agent: {__version__}", page)
        self.assertIn(">Actualizează</button>", page)
        zip_path, _ = make_release(version=__version__)
        self.app.cfg["update_url"] = zip_path.as_uri()
        status, _, page = self.setari_post("/setari/update", {}, cookie)
        self.assertEqual(status, 200)
        self.assertIn("Ai deja ultima versiune", page)
        zip_path, _ = make_release(version="9.0.0", edits=["drivers/fiscool.py"])      # driverul fiscool nu e cel instalat aici (fisco)
        self.app.cfg["update_url"] = zip_path.as_uri()
        status, _, page = self.setari_post("/setari/update", {}, cookie)
        self.assertIn("Nimic de actualizat", page)
        status, _, _ = self.setari_post("/setari/update", {})                           # fără autentificare
        self.assertEqual(status, 401)

    def test_the_port_list_requires_login_and_returns_what_was_detected(self):
        from unittest import mock
        from worldbit_fiscal import ports
        status, _, _ = self.call("GET", "/setari/ports")
        self.assertEqual(status, 401)
        cookie = self.login()
        gasite = [{"path": "/dev/serial/by-id/usb-X-if00", "label": "X", "detail": "d", "stable": True, "note": "", "udev_rule": ""}]
        with mock.patch.object(ports, "list_ports", lambda: gasite):
            status, _, data = self.call("GET", "/setari/ports", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["ports"], gasite)

    def test_the_settings_page_offers_port_detection_for_the_pos_and_the_drawer(self):
        cookie = self.login()
        _, _, page = self.call("GET", "/setari", headers={"Cookie": cookie})
        self.assertIn("data-target='p_hypercom_ecr_port'", page)
        self.assertIn("data-target='drawer_port'", page)
        self.assertIn("id='chip-casa'", page)
        self.assertIn("<dialog id='port-modal'>", page)          # lista de echipamente se alege într-o fereastră separată, nu în pagină
        self.assertNotIn("class='ports'", page)
        self.assertNotIn("<script src", page)                  # fără resurse externe: pagina merge și fără internet

    def test_vat_rates_endpoint_requires_login_and_fisco_cannot_read_them(self):
        status, _, _ = self.call("GET", "/setari/vat-rates")
        self.assertEqual(status, 401)
        cookie = self.login()
        status, _, _ = self.call("GET", "/setari/vat-rates", headers={"Cookie": cookie})
        self.assertEqual(status, 404)                             # driverul Fisco nu are citirea cotelor
        _, _, page = self.call("GET", "/setari", headers={"Cookie": cookie})
        self.assertNotIn("id='vat-btn'", page)

    def test_update_endpoint_returns_json_for_the_progress_window_and_text_page_otherwise(self):
        from tests.test_update import make_release
        from worldbit_fiscal.version import __version__
        cookie = self.login()
        zip_path, _ = make_release(version=__version__)
        self.app.cfg["update_url"] = zip_path.as_uri()
        status, hdrs, body = self.call("POST", "/setari/update", "", {"Cookie": cookie, "Accept": "application/json"})
        self.assertEqual(status, 200)
        self.assertIn("application/json", hdrs["Content-Type"])
        self.assertEqual(json.loads(body)["state"], "current")
        status, hdrs, body = self.call("POST", "/setari/update", "", {"Cookie": cookie})
        self.assertIn("text/html", hdrs["Content-Type"])
        self.assertIn("Ai deja ultima versiune", body)

    def test_update_status_requires_login_and_returns_the_last_result_and_log(self):
        status, _, _ = self.call("GET", "/setari/update-status")
        self.assertEqual(status, 401)
        cookie = self.login()
        upd = os.path.join(self.home, "update")
        os.makedirs(upd)
        with open(os.path.join(upd, "result.json"), "w", encoding="utf-8") as f:
            json.dump({"ok": False, "from": "1.0.1", "to": "1.0.2", "reason": "RuntimeError: configurare incompatibila"}, f)
        with open(os.path.join(upd, "last.txt"), "w", encoding="utf-8") as f:
            f.write("EȘEC: versiunea 1.0.2 nu a răspuns")
        status, _, body = self.call("GET", "/setari/update-status", headers={"Cookie": cookie})
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["result"]["reason"], "RuntimeError: configurare incompatibila")
        self.assertIn("nu a răspuns", data["log"])

    @unittest.skipUnless(shutil.which("node"), "node lipsește: sintaxa JavaScript din pagină nu se poate verifica")
    def test_the_javascript_in_the_settings_page_is_syntactically_valid(self):
        """Un singur caracter greșit în scriptul din pagină (ex. un rând nou într-un șir) îl strică pe tot: butoanele nu mai fac nimic."""
        import re
        import subprocess
        cookie = self.login()
        _, _, page = self.call("GET", "/setari", headers={"Cookie": cookie})
        scripturi = re.findall(r"<script>(.*?)</script>", page, re.S)
        self.assertTrue(scripturi)
        fisier = os.path.join(self.home, "pagina.js")
        with open(fisier, "w", encoding="utf-8") as f:
            f.write("\n".join(scripturi))
        r = subprocess.run(["node", "--check", fisier], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_login_survives_an_agent_restart(self):
        cookie = self.login()
        # agentul repornește (de exemplu la actualizare): aceeași dată, sesiuni noi încărcate din fișier
        self.app.sessions = auth.Sessions(os.path.join(self.home, "sessions.json"))
        status, _, page = self.call("GET", "/setari", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertNotIn("Autentificare", page)

    def test_health_reports_the_version(self):
        from worldbit_fiscal.version import __version__
        _, _, data = self.api("GET", "/health")
        self.assertEqual(data["version"], __version__)

    def test_setari_test_button_and_password_change(self):
        cookie = self.login()
        status, _, page = self.setari_post("/setari/test", {}, cookie)
        self.assertIn("pregătită", page)
        status, _, page = self.setari_post("/setari/password", {"new_password": "scurta"}, cookie)
        self.assertEqual(status, 400)
        status, _, page = self.setari_post("/setari/password", {"new_password": "parola-noua-22"}, cookie)
        self.assertEqual(status, 200)
        self.assertTrue(auth.verify_password("parola-noua-22", config_mod.load(self.home)["admin"]))


if __name__ == "__main__":
    unittest.main()


class FiscoolVatRatesServerTest(unittest.TestCase):
    def setUp(self):
        from tests.fakes import FakeFiscool, fiscool_cfg
        self.fiscool = FakeFiscool().start()
        self.addCleanup(self.fiscool.stop)
        self.home = tempfile.mkdtemp()
        cfg = fiscool_cfg(self.fiscool.url)
        cfg["admin"] = auth.hash_password("parola-admin-1")
        self.app = App(cfg, make_runner(cfg), self.home)
        self.httpd = make_server(self.app, port=0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read().decode()
        conn.close()
        return resp.status, dict(resp.getheaders()), data

    def login(self):
        status, hdrs, _ = self.call("POST", "/setari/login", urlencode({"password": "parola-admin-1"}),
                                    {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 303)
        return hdrs["Set-Cookie"].split(";")[0]

    def test_the_casa_tab_offers_the_vat_query_and_the_endpoint_returns_the_groups(self):
        cookie = self.login()
        _, _, page = self.call("GET", "/setari", headers={"Cookie": cookie})
        self.assertIn("id='vat-btn'", page)
        self.assertIn("<dialog id='vat-modal'>", page)
        status, _, data = self.call("GET", "/setari/vat-rates", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["groups"][0], {"letter": "A", "percent": "21"})

    def test_a_house_without_rates_gets_a_readable_error(self):
        cookie = self.login()
        self.fiscool.vat_rates = None
        status, _, data = self.call("GET", "/setari/vat-rates", headers={"Cookie": cookie})
        self.assertEqual(status, 502)
        self.assertIn("grupele de TVA", json.loads(data)["error"]["message"])
