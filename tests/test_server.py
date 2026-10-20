import http.client
import json
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
