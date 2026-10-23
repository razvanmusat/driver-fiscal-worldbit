"""Servere false (Fisco, FisCool) și utilitare pentru teste. Comportamentul Fisco urmează manualul fisco.ro;
cel FisCool urmează documentația publică (Idempotency-Key = același rezultat, fără reexecuție)."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from worldbit_fiscal.runner import Runner
from worldbit_fiscal.state import Store


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Server:
    def start(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.dispatch(self, "GET")

            def do_POST(self):
                outer.dispatch(self, "POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def dispatch(self, h, method):
        """Endpointuri de control pentru testele din afara procesului (ex. testele end-to-end din Dressbit):
        GET /__state = ce a primit serverul fals; POST /__control = schimbă comportamentul lui."""
        path = h.path.split("?", 1)[0]
        if path == "/__state" and method == "GET":
            return self.reply(h, 200, self.state())
        if path == "/__control" and method == "POST":
            self.control(self.body(h) or {})
            return self.reply(h, 200, self.state())
        self.handle(h, method)

    def state(self):
        return {}

    def control(self, body):
        pass

    @staticmethod
    def reply(h, status, obj):
        data = json.dumps(obj).encode()
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    @staticmethod
    def drop(h):
        h.connection.shutdown(socket.SHUT_RDWR)
        h.close_connection = True

    @staticmethod
    def body(h):
        n = int(h.headers.get("Content-Length", "0"))
        return json.loads(h.rfile.read(n)) if n else None


class FakeFisco(_Server):
    def __init__(self):
        self.ready = True
        self.ready_error = "Depasire 24h Raport Z imprimanta fiscala"   # motivul afisat cand nu e pregatita
        self.not_ready_times = 0      # primele N raspunsuri la /health spun „nepregatita”, apoi revine (ex. „Printer status is not known yet”)
        self.mode = "normal"          # normal | reject | drop_before | drop_after | fail
        self.polls_until_printed = 1
        self.jobs = []                # cele mai noi primele
        self.posts = []
        self.print_count = 0
        self.forgotten = set()

    def state(self):
        return {"print_count": self.print_count, "posts": self.posts, "jobs": [j["job_id"] for j in self.jobs],
                "ready": self.ready, "mode": self.mode}

    def control(self, body):
        for key in ("ready", "mode", "polls_until_printed", "ready_error", "not_ready_times"):
            if key in body:
                setattr(self, key, body[key])
        if "forgotten" in body:
            self.forgotten = set(body["forgotten"])

    def handle(self, h, method):
        path, _, query = h.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if method == "GET" and path == "/api/v1/health":
            ready = self.ready
            if self.not_ready_times > 0:
                self.not_ready_times -= 1
                ready = False
            return self.reply(h, 200, {"ok": True, "printer": {
                "ready": ready, "status": "ready" if ready else "not_ready", "error": "" if ready else self.ready_error}})
        if method == "GET" and path == "/api/v1/status":
            if "job_id" in params:
                return self.reply(h, 200, self._status(params["job_id"]))
            limit = int(params.get("limit", 100))
            return self.reply(h, 200, {"ok": True, "status": "latest", "count": len(self.jobs[:limit]),
                                       "jobs": [self._status(j["job_id"]) for j in self.jobs[:limit]]})
        if method == "POST" and path == "/api/v1/print":
            body = self.body(h)
            self.posts.append(body)
            if self.mode == "drop_before":
                return self.drop(h)
            if self.mode == "reject":
                return self.reply(h, 200, {"ok": False, "error": {"code": "CONVERSION_ERROR", "message": "#Linia 2 | invalid"}})
            job = {"job_id": f"http_{len(self.jobs) + 1:04d}", "polls": 0, "external_id": body.get("external_id"),
                   "commands": body.get("commands")}
            self.jobs.insert(0, job)
            self.print_count += 1
            if self.mode == "drop_after":
                return self.drop(h)
            return self.reply(h, 200, {"ok": True, "job_id": job["job_id"], "status": "queued"})
        self.reply(h, 404, {"ok": False})

    def _status(self, job_id):
        job = next((j for j in self.jobs if j["job_id"] == job_id), None)
        if job is None or job_id in self.forgotten:
            return {"ok": True, "job_id": job_id, "status": "not_found"}
        job["polls"] += 1
        if self.mode == "fail":
            return {"ok": True, "job_id": job_id, "status": "failed", "result": {},
                    "error": {"code": "PRINT_FAILED", "message": "#Linia 2 | eroare"}}
        if job["polls"] < self.polls_until_printed:
            return {"ok": True, "job_id": job_id, "status": "processing"}
        n = int(job_id.split("_")[1])
        is_report = job["commands"] in (["X^"], ["Z^"])
        result = ({"type": "report", "CommandName": "reports", "ErrorCode": "0", "nRep": "431", "DeviceSerial": "DB41"}
                  if is_report else
                  {"type": "receipt", "CommandName": "receipt_Fiscal_Close", "ErrorCode": "0",
                   "SlipNumber": str(6000 + n), "nZrep": "430", "nFNum": str(n), "DeviceSerial": "DB41"})
        return {"ok": True, "job_id": job_id, "status": "printed", "result": result}


class FakeFiscool(_Server):
    """Forma răspunsurilor urmează documentația FisCool (api-http, operatiuni). Succesul se memorează pe cheie (documentat); erorile
    tot, cu excepția celor documentate ca reîncercabile pe aceeași cheie (408, 605, 606, 1005). Verificat pe FisCool real (casă
    simulată) doar pentru eroarea 500 „Lipsă hârtie": aceeași cheie întoarce aceeași eroare chiar cu hârtia pusă. Pentru 408 etc. nu s-a putut verifica."""
    DEVICE = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
    SERIAL = "DT123456"

    def __init__(self):
        self.mode = "normal"          # normal | drop_after
        self.error = None             # (http, cod, mesaj) -> răspunde cu eroare
        self.error_times = None       # None = mereu; N = doar primele N cereri primesc eroarea, apoi merge
        self.device_status = "Connected"
        self.device_listed = True
        self.vat_rates = {"A": 2100, "B": 1100, "C": 0}   # forma din documentația API HTTP (litera grupei -> sutimi de procent); nevăzută pe un răspuns real
        self.requests = []            # (Idempotency-Key, corp)
        self.cache = {}
        self.error_cache = {}
        self.executed = 0
        self.slip_base = 1233         # primul bon are numărul slip_base + 1; două case false în aceeași bază de date au nevoie de numerotări diferite

    def state(self):
        return {"executed": self.executed, "requests": [[k, b] for k, b in self.requests], "mode": self.mode,
                "device_status": self.device_status, "error": list(self.error) if self.error else None}

    def control(self, body):
        for key in ("mode", "device_status", "device_listed", "error_times", "slip_base"):
            if key in body:
                setattr(self, key, body[key])
        if "error" in body:
            self.error = tuple(body["error"]) if body["error"] else None

    def handle(self, h, method):
        if method == "GET" and h.path == "/devices/fiscal":
            devices = []
            if self.device_listed:
                devices.append({
                    "config": {"id": self.DEVICE, "name": "Casa 1", "enabled": True, "auto_send_card_to_pos": False},
                    "status": self.device_status, "status_text": self.device_status,
                    "vat_rates": self.vat_rates,
                    "connected_device_id": self.SERIAL if self.device_status == "Connected" else None})
            return self.reply(h, 200, {"fiscal_devices": devices})
        if method == "POST" and h.path == "/devices/operation":
            key = h.headers.get("Idempotency-Key")
            body = self.body(h)
            self.requests.append((key, body))
            if key in self.cache:
                return self.reply(h, 200, self.cache[key])
            if key in self.error_cache:
                return self.reply(h, *self.error_cache[key])
            if self.error:
                if self.error_times is not None:
                    self.error_times -= 1
                http, code, message, *rest = self.error
                if self.error_times is not None and self.error_times <= 0:
                    self.error = None   # eroarea trecătoare a trecut: următoarea cerere merge
                corp = {"status": code, "error": message}
                if rest:
                    corp["receipt"] = rest[0]   # chitanța de refuz POS (câmp opțional documentat)
                if code not in (408, 605, 606, 1005):
                    self.error_cache[key] = (http, corp)
                return self.reply(h, http, corp)
            self.executed += 1
            kind = body["operation"]["type"]
            if kind == "print_receipt":
                obj = {"status": 0, "msg": "Receipt printed", "slip_number": self.slip_base + self.executed, "z_report_number": 45,
                       "fiscal_receipt_count": 1050, "bon_id": None}
            else:
                obj = {"status": 0, "msg": "Report printed", "report_number": 46, "grand_total_sell": 12345,
                       "payment_totals": [{"method": "cash", "amount": 12345}], "vat_totals": [], "full_text": None}
            self.cache[key] = obj
            if self.mode == "drop_after":
                self.mode = "normal"   # doar prima trimitere își pierde răspunsul
                return self.drop(h)
            return self.reply(h, 200, obj)
        self.reply(h, 404, {"status": 404, "error": "nu există"})


def receipt_json(external_id="r1", card=0, cash=None, qty=1):
    price = 100
    total = price * qty
    cash = total - card if cash is None else cash
    payments = []
    if cash:
        payments.append({"method": "cash", "amount": f"{cash:.2f}"})
    if card:
        payments.append({"method": "card", "amount": f"{card:.2f}"})
    return json.dumps({
        "external_id": external_id,
        "lines": [{"name": "Rochie roșie M", "unit_price": "100.00", "quantity": qty, "vat_percent": 21, "unit": "buc"}],
        "payments": payments,
        "total": f"{total:.2f}",
    })


def make_runner(cfg):
    return Runner(cfg, Store(":memory:"))


def fisco_cfg(url, **driver_overrides):
    settings = {"base_url": url, "poll_interval_s": 0.01, "poll_timeout_s": 2}
    settings.update(driver_overrides)
    return {"port": 4747, "allowed_origins": [], "driver": "fisco", "drivers": {"fisco": settings},
            "pos": {"model": "none", "settings": {}}, "auto_retry": {"max": 3, "delay_s": 0.02}, "admin": None}


def fiscool_cfg(url, device_id=FakeFiscool.DEVICE, **driver_overrides):
    settings = {"base_url": url, "device_id": device_id}
    settings.update(driver_overrides)
    return {"port": 4747, "allowed_origins": [], "driver": "fiscool", "drivers": {"fiscool": settings},
            "pos": {"model": "none", "settings": {}}, "auto_retry": {"max": 3, "delay_s": 0.02}, "admin": None}


def wait_status(runner, external_id, statuses, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = runner.store.get(external_id)
        if row and row["status"] in statuses:
            return row
        time.sleep(0.01)
    raise AssertionError(f"jobul {external_id} nu a ajuns în {statuses}: {runner.store.get(external_id)}")


def resolve_now(runner, external_id):
    """Forțează lămurirea imediată (în producție GET /jobs o declanșează la fiecare 3 secunde)."""
    runner.store.update(external_id, last_resolve=0)
    return runner.get(external_id)
