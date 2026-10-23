"""Serverul HTTP al agentului: doar pe 127.0.0.1. API pentru aplicația web + pagina /setari.

Apărări (fără certificat, cum s-a hotărât):
  - bind strict pe loopback;
  - antetul Host trebuie să fie 127.0.0.1:<port> sau localhost:<port> (împotriva DNS rebinding);
  - dacă cererea are Origin, el trebuie să fie în `allowed_origins` (API) sau să fie chiar agentul (/setari);
  - /setari cere parola de admin (cookie HttpOnly + SameSite=Strict).
"""
import json
import logging
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

from . import auth, config as config_mod, drawer, ports, setari, updater
from .contract import InvalidPayload
from .drivers import DRIVERS, build_driver, driver_settings
from .pos.base import build_pos
from .runner import public_job
from .version import __version__

log = logging.getLogger("worldbit_fiscal")

MAX_BODY = 1_000_000
_JOB_PATH = re.compile(r"^/jobs/([A-Za-z0-9._-]{1,64})(/retry|/pos-void)?$")


class App:
    """Starea comună a serverului."""

    def __init__(self, cfg, runner, home=None):
        self.cfg = cfg
        self.runner = runner
        self.home = home
        self.sessions = auth.Sessions(os.path.join(home, "sessions.json") if home else None)
        self.throttle = auth.LoginThrottle()


def make_server(app, host="127.0.0.1", port=None):
    handler = type("BoundHandler", (Handler,), {"app": app})
    return ThreadingHTTPServer((host, port if port is not None else app.cfg["port"]), handler)


class Handler(BaseHTTPRequestHandler):
    app = None
    server_version = "WorldbitFiscalDriver"
    sys_version = ""

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------------ verificări comune
    def _port(self):
        return self.server.server_address[1]

    def _host_ok(self):
        return self.headers.get("Host") in (f"127.0.0.1:{self._port()}", f"localhost:{self._port()}")

    def _own_origins(self):
        return (f"http://127.0.0.1:{self._port()}", f"http://localhost:{self._port()}")

    def _guard(self, is_setari):
        """Întoarce True dacă cererea poate continua; altfel a răspuns deja cu 403."""
        if not self._host_ok():
            self._json(403, {"ok": False, "error": {"code": "FORBIDDEN_HOST", "message": "Host nepermis"}})
            return False
        origin = self.headers.get("Origin")
        if origin is not None:
            allowed = self._own_origins() if is_setari else tuple(self.app.cfg["allowed_origins"])
            if origin not in allowed:
                self._json(403, {"ok": False, "error": {"code": "FORBIDDEN_ORIGIN", "message": "Origine nepermisă"}})
                return False
        return True

    # ------------------------------------------------------------------ răspunsuri
    def _cors_headers(self):
        origin = self.headers.get("Origin")
        if origin and origin in self.app.cfg["allowed_origins"]:
            return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
        return {}

    def _send(self, status, body, content_type, extra=None):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")   # serverul închide conexiunea după fiecare răspuns: clienții nu trebuie s-o refolosească
        self.send_header("Cache-Control", "no-store")
        for k, v in {**self._cors_headers(), **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, obj, extra=None):
        self._send(status, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8", extra)

    def _html(self, status, body, extra=None):
        self._send(status, body, "text/html; charset=utf-8", extra)

    def _redirect(self, location, extra=None):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            return None
        return self.rfile.read(length).decode("utf-8", "replace")

    def _error(self, status, code, message):
        self._json(status, {"ok": False, "error": {"code": code, "message": message}})

    # ------------------------------------------------------------------ rutare
    def do_OPTIONS(self):
        if not self._guard(False):
            return
        origin = self.headers.get("Origin")
        if origin and origin in self.app.cfg["allowed_origins"]:
            self.send_response(204)
            extra = {}
            if self.headers.get("Access-Control-Request-Private-Network", "").lower() == "true":
                extra["Access-Control-Allow-Private-Network"] = "true"   # Chrome o cere când un site public apelează un agent local (Private/Local Network Access)
            for k, v in {**self._cors_headers(),
                         "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                         "Access-Control-Allow-Headers": "Content-Type",
                         "Access-Control-Max-Age": "600", **extra}.items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
        else:
            self._error(403, "FORBIDDEN_ORIGIN", "Origine nepermisă")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.startswith("/setari"):
            return self._setari_get(path)
        if not self._guard(False):
            return
        if path == "/health":
            return self._health()
        match = _JOB_PATH.match(path)
        if match and not match.group(2):
            row = self.app.runner.get(match.group(1))
            if row is None:
                return self._error(404, "NOT_FOUND", "Job necunoscut")
            return self._json(200, public_job(row, self.app.runner.fiscal.name, self.app.cfg.get("agent_id")))
        self._error(404, "NOT_FOUND", "Cale necunoscută")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path.startswith("/setari"):
            return self._setari_post(path)
        if not self._guard(False):
            return
        body = self._body()
        if body is None:
            return self._error(413, "BODY_TOO_LARGE", "Corp prea mare")
        runner = self.app.runner
        try:
            if path == "/print":
                row, created = runner.submit_receipt(body)
                return self._json(202 if created else 200, public_job(row, runner.fiscal.name, self.app.cfg.get("agent_id")))
            if path == "/drawer/open":
                ok, cod, mesaj = drawer.open_drawer(self.app.cfg)
                if ok:
                    return self._json(200, {"ok": True})
                return self._error(409 if cod == "DRAWER_NOT_CONFIGURED" else 502, cod, mesaj)
            if path == "/reports":
                data = json.loads(body)
                row, created = runner.submit_report(data.get("external_id"), data.get("type"))
                return self._json(202 if created else 200, public_job(row, runner.fiscal.name, self.app.cfg.get("agent_id")))
            match = _JOB_PATH.match(path)
            if match and match.group(2) == "/pos-void":
                return self._pos_void(match.group(1))
            if match and match.group(2):
                data = json.loads(body) if body.strip() else {}
                row, problem = runner.retry(match.group(1), bool(data.get("confirm_not_printed")))
                if problem == "NOT_FOUND":
                    return self._error(404, "NOT_FOUND", "Job necunoscut")
                if problem == "NOT_FAILED":
                    return self._error(409, "NOT_FAILED", "Doar un job eșuat sau incert poate fi reîncercat")
                if problem == "CONFIRMATION_REQUIRED":
                    return self._error(409, "CONFIRMATION_REQUIRED",
                                       "Verifică aparatul: dacă bonul NU a ieșit, retrimite cu confirm_not_printed=true")
                return self._json(202, public_job(row, runner.fiscal.name, self.app.cfg.get("agent_id")))
        except InvalidPayload as e:
            return self._error(400, "INVALID_PAYLOAD", str(e))
        except (ValueError, AttributeError):
            return self._error(400, "INVALID_PAYLOAD", "JSON invalid")
        self._error(404, "NOT_FOUND", "Cale necunoscută")

    def _pos_void(self, external_id):
        """Anulează la POS plata aprobată a unui bon care n-a ieșit (vezi Runner.void_pos)."""
        runner = self.app.runner
        row, problem, eroare = runner.void_pos(external_id)
        if problem is None:
            return self._json(200, public_job(row, runner.fiscal.name, self.app.cfg.get("agent_id")))
        if problem == "NOT_FOUND":
            return self._error(404, "NOT_FOUND", "Job necunoscut")
        if problem == "NOT_VOIDABLE":
            return self._error(409, "NOT_VOIDABLE", "Plata nu poate fi anulată: bonul a ieșit sau plata nu e aprobată la POS")
        if problem == "BUSY":
            return self._error(409, "BUSY", "O operațiune fiscală e în curs; încearcă din nou")
        nr = ((row or {}).get("pos") or {}).get("transaction_id")
        sufix = f" Anulează chitanța nr. {nr} direct de pe POS." if nr else ""
        return self._error(502, eroare.code, f"{eroare}{sufix}")

    def _health(self):
        runner = self.app.runner
        health = runner.fiscal.health()
        self._json(200, {
            "ok": True,
            "version": __version__,
            "agent_id": self.app.cfg.get("agent_id"),
            "driver": runner.fiscal.name,
            "fiscal": {"ready": health.ready, "detail": health.detail},
            "pos": {"model": self.app.cfg["pos"]["model"], "configured": runner.pos is not None},
            "drawer": {"configured": drawer.configured(self.app.cfg)},
        })

    # ------------------------------------------------------------------ /setari
    def _session_token(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("wsid")
        return morsel.value if morsel else None

    def _authed(self):
        return self.app.sessions.valid(self._session_token())

    def _settings_html(self, **kw):
        cfg = self.app.cfg
        cls = DRIVERS[cfg["driver"]]
        return setari.settings_page(cfg, cls, driver_settings(cfg, cfg["driver"]),
                                    last_update=updater.last_log(self.app.home or config_mod.home_dir()), **kw)

    def _setari_get(self, path):
        if not self._guard(True):
            return
        if path not in ("/setari", "/setari/ports", "/setari/update-status", "/setari/vat-rates"):
            return self._error(404, "NOT_FOUND", "Cale necunoscută")
        if path in ("/setari/ports", "/setari/update-status", "/setari/vat-rates"):
            if not self._authed():
                return self._error(401, "UNAUTHORIZED", "Autentificare necesară")
            if path == "/setari/vat-rates":
                return self._vat_rates()
            if path == "/setari/ports":
                return self._json(200, {"ports": ports.list_ports()})
            home = self.app.home or config_mod.home_dir()
            return self._json(200, {"result": updater.update_result(home), "log": updater.last_log(home)})
        if not self._authed():
            return self._html(200, setari.login_page(configured=bool(self.app.cfg["admin"])))
        self._html(200, self._settings_html())

    def _vat_rates(self):
        """Grupele de TVA ale casei de marcat (citite de driver), pentru fereastra de alegere din /setari."""
        vat_groups = getattr(self.app.runner.fiscal, "vat_groups", None)
        if vat_groups is None:
            return self._error(404, "NOT_FOUND", "Driverul nu poate citi cotele de TVA ale casei")
        try:
            return self._json(200, {"groups": vat_groups()})
        except ValueError as e:
            return self._error(502, "VAT_RATES_UNAVAILABLE", str(e))

    def _form(self):
        body = self._body()
        if body is None:
            return None
        return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}

    def _setari_post(self, path):
        if not self._guard(True):
            return
        form = self._form()
        if form is None:
            return self._error(413, "BODY_TOO_LARGE", "Corp prea mare")
        app = self.app

        if path == "/setari/login":
            if not app.cfg["admin"]:
                return self._html(200, setari.login_page(configured=False))
            if app.throttle.blocked():
                return self._html(429, setari.login_page("Prea multe încercări. Așteaptă un minut."))
            if not auth.verify_password(form.get("password", ""), app.cfg["admin"]):
                app.throttle.failed()
                return self._html(401, setari.login_page("Parolă greșită."))
            app.throttle.succeeded()
            token = app.sessions.create()
            return self._redirect("/setari", {
                "Set-Cookie": f"wsid={token}; HttpOnly; SameSite=Strict; Path=/setari"})

        if not self._authed():
            return self._html(401, setari.login_page("Sesiune expirată."))

        if path == "/setari/logout":
            app.sessions.drop(self._session_token())
            return self._redirect("/setari", {"Set-Cookie": "wsid=; Max-Age=0; Path=/setari"})

        if path == "/setari/save":
            try:
                new_cfg = setari.parse_settings_form(form, app.cfg, DRIVERS[app.cfg["driver"]])
                build_driver(new_cfg)   # validează înainte de salvare
                build_pos(new_cfg)
            except ValueError as e:
                return self._html(400, self._settings_html(error=str(e)))
            config_mod.save(new_cfg, app.home)
            app.cfg.clear()
            app.cfg.update(new_cfg)
            app.runner.apply_config(app.cfg)
            return self._html(200, self._settings_html(message="Setările au fost salvate."))

        if path == "/setari/test":
            health = app.runner.fiscal.health()
            text = ("Casa de marcat este pregătită." if health.ready
                    else f"Casa de marcat NU este pregătită: {health.detail}")
            return self._html(200, self._settings_html(output=text))

        if path == "/setari/drawer-test":
            ok, cod, mesaj = drawer.open_drawer(app.cfg)
            return self._html(200, self._settings_html(output="Sertarul s-a deschis." if ok else f"Sertarul NU s-a deschis: {mesaj}"))

        if path == "/setari/update":
            info = updater.run_update_info(app.cfg, app.runner, app.home or config_mod.home_dir())
            if "application/json" in (self.headers.get("Accept") or ""):
                return self._json(200, info)                  # fereastra de progres din pagină
            return self._html(200, self._settings_html(output=info["message"]))

        if path == "/setari/pos-test":
            pos = app.runner.pos
            check = getattr(pos, "check_port", None)
            text = "POS-ul nu e configurat (model: none)." if pos is None else (check() if check else "Modelul POS nu are verificare de port.")
            return self._html(200, self._settings_html(output=text))

        if path == "/setari/devices":
            discover = getattr(app.runner.fiscal, "discover", None)
            if discover is None:
                return self._error(404, "NOT_FOUND", "Driverul nu suportă detectarea")
            return self._html(200, self._settings_html(output=discover()))

        if path == "/setari/password":
            new_password = form.get("new_password", "")
            if len(new_password) < 8:
                return self._html(400, self._settings_html(error="Parola trebuie să aibă minim 8 caractere."))
            app.cfg["admin"] = auth.hash_password(new_password)
            config_mod.save(app.cfg, app.home)
            return self._html(200, self._settings_html(message="Parola a fost schimbată."))

        self._error(404, "NOT_FOUND", "Cale necunoscută")
