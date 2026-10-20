"""Orchestrarea unui job: health casă -> (POS, dacă e configurat) -> tipărire fiscală. Un singur job în zbor (SEND_LOCK),
ca în Sellbit: cu un singur POST în zbor, reconcilierea pe snapshot rămâne neambiguă."""
import json
import logging
import threading
import time

from . import drawer
from .contract import InvalidPayload, parse_json, parse_receipt, validate_external_id
from .drivers import build_driver
from .errors import DriverError, NotConnected, Uncertain
from .pos.base import build_pos

log = logging.getLogger("worldbit_fiscal")

RESOLVE_THROTTLE_S = 3.0


class JobHandle:
    """Ce văd driverele din job: cheia de idempotență, starea lor privată persistată, job_id-ul fiscal."""

    def __init__(self, store, row):
        self._store = store
        self.external_id = row["external_id"]
        self.kind = row["kind"]
        self.attempt = row["attempt"]
        self.state = row["state"]
        self.fiscal_job_id = row["fiscal_job_id"]
        self.pos = row["pos"]
        self.payload = row["payload"]

    @property
    def key(self):
        """Cheia trimisă aparatului: prima încercare = external_id; reîncercările au sufix (altfel un
        external_id deja eșuat ar putea întoarce rezultatul vechi)."""
        return self.external_id if self.attempt == 1 else f"{self.external_id}-{self.attempt}"

    def save(self):
        self._store.update(self.external_id, state=self.state)

    def set_fiscal_job_id(self, job_id):
        self.fiscal_job_id = job_id
        self._store.update(self.external_id, fiscal_job_id=job_id)

    def job_id_in_use(self, job_id):
        return self._store.job_id_in_use(job_id, self.external_id)

    def set_pos(self, pos_dict):
        self.pos = pos_dict
        self._store.update(self.external_id, pos=pos_dict)

    def receipt(self):
        return parse_receipt(parse_json(self.payload))

    def report_type(self):
        return json.loads(self.payload)["type"]


class Runner:
    def __init__(self, cfg, store):
        self.store = store
        self.send_lock = threading.RLock()
        self.apply_config(cfg)

    def apply_config(self, cfg):
        """Înlocuiește driverele (referințe atomice); joburile în curs își păstrează driverul cu care au pornit."""
        self.cfg = cfg
        self.fiscal = build_driver(cfg)
        self.pos = build_pos(cfg)

    # ------------------------------------------------------------------ intrări
    def submit_receipt(self, body_text):
        """Întoarce (rând, creat). Ridică InvalidPayload. Același external_id cu același conținut = același job.
        Un external_id NOU cu un payment_key deja cunoscut nu mai tipărește: devine o oglindă a jobului care execută plata
        (același bon refăcut după o anulare fără dovadă că nu a ieșit: net căzut, agent inaccesibil, fereastră închisă)."""
        receipt = parse_receipt(parse_json(body_text))
        with self.store.atomic():
            existent = self.store.get(receipt.external_id)
            if existent is not None:
                if parse_receipt(parse_json(existent["payload"])).canonical() != receipt.canonical():
                    raise InvalidPayload("external_id folosit deja cu alt conținut")
                return self._urmeaza(existent), False
            primar = self.store.primary_for_payment_key(receipt.payment_key, receipt.external_id) if receipt.payment_key else None
            if primar is not None:
                if parse_receipt(parse_json(primar["payload"])).content() != receipt.content():
                    raise InvalidPayload("payment_key folosit deja cu alt conținut")
                row, _ = self.store.create(receipt.external_id, "receipt", body_text, payment_key=receipt.payment_key,
                                           status="alias", state={"alias_of": primar["external_id"]})
                log.info("jobul %s urmează jobul %s (același payment_key): nu se tipărește a doua oară", receipt.external_id, primar["external_id"])
                return self._urmeaza(row), False
            row, _ = self.store.create(receipt.external_id, "receipt", body_text, payment_key=receipt.payment_key)
        self._start(receipt.external_id)
        return self.store.get(receipt.external_id), True

    def submit_report(self, external_id, kind):
        validate_external_id(external_id)
        if kind not in ("X", "Z"):
            raise InvalidPayload("type: X sau Z")
        payload = json.dumps({"external_id": external_id, "type": kind})
        row, created = self.store.create(external_id, "report", payload)
        if not created:
            if row["kind"] != "report" or json.loads(row["payload"]).get("type") != kind:
                raise InvalidPayload("external_id folosit deja cu alt conținut")
            return row, False
        self._start(external_id)
        return self.store.get(external_id), True

    def _urmeaza(self, row):
        """Un job-oglindă (alias) arată starea jobului care execută plata, sub propriul external_id; orice alt job rămâne cum e."""
        if row is None or row["status"] != "alias":
            return row
        tinta = self.get(row["state"]["alias_of"])
        if tinta is None:   # nu ar trebui să se întâmple (jobul nu se șterge): nu se știe nimic, deci incert, fără tipărire
            return {**row, "status": "uncertain", "error": {"code": "ALIAS_ORFAN", "message": "Jobul plății nu mai există în agent", "retry_safe": False}}
        return {**tinta, "external_id": row["external_id"], "alias_of": tinta["external_id"]}

    def retry(self, external_id, confirm_not_printed):
        """Reîncercare după eșec (sau după incertitudine, doar cu confirmarea operatorului)."""
        row = self.store.get(external_id)
        if row is None:
            return None, "NOT_FOUND"
        if row["status"] == "alias":   # reîncercarea se face pe jobul care execută plata
            rez, problema = self.retry(row["state"]["alias_of"], confirm_not_printed)
            return (self._urmeaza(self.store.get(external_id)) if rez is not None else None), problema
        if row["status"] not in ("failed", "uncertain"):
            return row, "NOT_FAILED"
        # Un job incert nu e niciodată „sigur”: reîncercarea cere confirmarea operatorului că bonul NU a ieșit.
        retry_safe = row["status"] == "failed" and bool((row["error"] or {}).get("retry_safe"))
        if not retry_safe and not confirm_not_printed:
            return row, "CONFIRMATION_REQUIRED"
        self.store.update(external_id, status="received", attempt=row["attempt"] + 1, fiscal_job_id=None,
                          state={}, error=None, result=None, last_resolve=0, started_at=time.time())
        self._start(external_id)
        return self.store.get(external_id), None

    def _termen(self, row):
        """Cât timp (secunde) se așteaptă confirmarea unui job, de la pornirea lui."""
        cheie, implicit = ("max_wait_report_s", 120) if row["kind"] == "report" else ("max_wait_s", 30)
        return float(self.cfg.get(cheie, implicit))

    def _abandoneaza(self, external_id):
        """Termenul a trecut și jobul tot nu e confirmat ca emis: nu mai e considerat în așteptare. Devine un eșec cu retrimitere sigură,
        ca plata să se poată relua normal. ÎNREGISTRAREA rămâne în agent (nu se șterge): `fiscal_job_id` al unui job abandonat trebuie să
        rămână atribuit lui, ca reconcilierea unui job următor să nu-l adopte din greșeală. Fisco / FisCool nu au comandă de anulare:
        dacă driverul îl emite totuși mai târziu, agentul nu mai are cum să-l oprească."""
        row = self.store.get(external_id)
        if row is None or row["status"] != "uncertain":
            return
        if (row["state"] or {}).get("stage") == "pos":
            return   # plata cu cardul e nelămurită: nu se abandonează (ar permite o a doua debitare)
        secunde = int(self._termen(row))
        obiect = "Raportul" if row["kind"] == "report" else "Bonul"
        self.store.update(external_id, status="failed", last_resolve=time.time(), error={
            "code": "NOT_CONFIRMED",
            "message": f"{obiect} nu a fost confirmat ca emis în {secunde} de secunde, deci nu este considerat emis. Plata se poate relua.",
            "retry_safe": True,
            "ultima_eroare": row["error"],
        })

    def get(self, external_id):
        row = self.store.get(external_id)
        if row is not None and row["status"] == "alias":
            return self._urmeaza(row)
        if row is not None and row["status"] == "uncertain" and time.time() - row["started_at"] > self._termen(row):
            if self.send_lock.acquire(blocking=False):   # nu blocăm citirea cât rulează un alt job
                try:
                    self._abandoneaza(external_id)
                finally:
                    self.send_lock.release()
            return self.store.get(external_id)
        if row is not None and row["status"] == "uncertain" and time.time() - row["last_resolve"] >= RESOLVE_THROTTLE_S:
            if self.send_lock.acquire(blocking=False):   # nu blocăm citirea cât rulează un alt job
                try:
                    self._resolve(external_id)
                finally:
                    self.send_lock.release()
                row = self.store.get(external_id)
        return row

    def recover(self):
        """La pornire: joburile rămase în mijloc (agent oprit) devin incerte și se lămuresc la prima citire."""
        for row in self.store.unfinished():
            self.store.update(row["external_id"], status="uncertain", last_resolve=0,
                              error={"code": "RESTARTED", "message": "Agentul a fost repornit în timpul jobului",
                                     "retry_safe": False})

    # ------------------------------------------------------------------ execuție
    def _start(self, external_id):
        threading.Thread(target=self._execute, args=(external_id,), daemon=True,
                         name=f"job-{external_id}").start()

    def _execute(self, external_id):
        """Rulează jobul. Eșecurile TRECĂTOARE documentate de drivere (casă ocupată, conexiune încă neridicată) se reîncearcă singure,
        de câteva ori, fără operator; restul se transmite ca atare (succes, eșec, incert)."""
        fiscal, pos = self.fiscal, self.pos
        auto = self.cfg.get("auto_retry") or {}
        maxim, pauza = int(auto.get("max", 3)), float(auto.get("delay_s", 3.0))
        with self.send_lock:
            reincercari = 0
            while True:
                job = JobHandle(self.store, self.store.get(external_id))
                eroare = self._incercare(job, fiscal, pos, prima=reincercari == 0)
                if eroare is None:
                    return
                termen_depasit = time.time() - self.store.get(external_id)["started_at"] > self._termen({"kind": job.kind})
                if eroare.transient and reincercari < maxim and not termen_depasit:
                    reincercari += 1
                    info = {**eroare.to_dict(), "auto_retry": {"count": reincercari, "max": maxim}}
                    # încercare nouă (cheie nouă la driver), jobul rămâne „în lucru”; eroarea trecătoare rămâne vizibilă ca informație
                    self.store.update(external_id, status="running", attempt=job.attempt + 1, fiscal_job_id=None, state={}, error=info)
                    time.sleep(pauza)
                    continue
                if reincercari:
                    eroare.extra["auto_retry"] = {"count": reincercari, "max": maxim, "epuizat": True}
                self._fail(job, eroare)
                return

    def _incercare(self, job, fiscal, pos, prima):
        """O singură încercare. Întoarce None la succes, altfel eroarea driverului."""
        try:
            if prima:
                self.store.update(job.external_id, status="running", error=None)
            else:
                self.store.update(job.external_id, status="running")
            if job.kind == "receipt":
                # Health doar la bon: raportul Z e tocmai ce deblochează aparatul după depășirea celor 24h,
                # deci nu îl blocăm cu health; dacă aparatul chiar nu poate, driverul respinge singur (safe).
                health = fiscal.health()
                if not health.ready:
                    raise NotConnected(health.detail or "Casa de marcat nu e pregătită", transient=health.transient, **health.extra)
                receipt = job.receipt()
                self._pos_step(pos, receipt, job)
                result = fiscal.print_receipt(receipt, job)
            else:
                result = fiscal.print_report(job.report_type(), job)
            self._printed(job, result)
            return None
        except DriverError as e:
            return e
        except Exception as e:   # orice altceva: nu știm cât s-a ajuns, deci incert (se lămurește prin resolve)
            log.exception("eroare internă la jobul %s", job.external_id)
            return Uncertain(f"Eroare internă: {e}", code="INTERNAL")

    def _pos_step(self, pos, receipt, job):
        """POS înaintea bonului fiscal. O plată deja aprobată nu se mai cere a doua oară la reîncercare."""
        if receipt.card_total <= 0:
            return
        if pos is None:
            job.set_pos({"status": "skipped", "reason": "pos_not_configured"})
            return
        if job.pos and job.pos.get("status") == "approved":
            return
        job.state["stage"] = "pos"       # dacă agentul cade în timpul plății, la recuperare se lămurește POS-ul, nu bonul
        job.save()
        result = pos.sale(receipt.card_total, job)
        job.set_pos(result.to_dict())
        job.state.pop("stage", None)
        job.save()

    def _resolve(self, external_id):
        row = self.store.get(external_id)
        job = JobHandle(self.store, row)
        self.store.update(external_id, last_resolve=time.time())
        try:
            if job.state.get("stage") == "pos":
                self._resolve_pos(job)
                return
            result = self.fiscal.resolve(job)
            self._printed(job, result)
        except DriverError as e:
            self._fail(job, e)
        except Exception as e:
            log.exception("eroare internă la lămurirea jobului %s", external_id)
            self._fail(job, Uncertain(f"Eroare internă: {e}", code="INTERNAL"))

    def _resolve_pos(self, job):
        """Plata cu cardul a rămas incertă: nu se retrimite NICIODATĂ cererea de plată până nu se lămurește."""
        if self.pos is None:
            raise Uncertain("Plata cu cardul a rămas incertă și POS-ul nu mai e configurat", code="POS_UNCERTAIN")
        result = self.pos.resolve(job)            # PosDeclined / Uncertain se propagă către _resolve
        job.set_pos(result.to_dict())
        job.state.pop("stage", None)
        job.save()
        # Plata e confirmată, bonul NU s-a tipărit încă: reîncercarea sare peste POS și tipărește.
        self._fail(job, DriverError("Plata cu cardul a fost confirmată; reîncearcă tipărirea bonului",
                                    code="POS_CONFIRMED", retry_safe=True))

    def _printed(self, job, result):
        self.store.update(job.external_id, status="printed", result=result.to_dict(), error=None)
        self._deschide_sertarul(job)

    def _deschide_sertarul(self, job):
        """Bon tipărit cu numerar (și la plata mixtă) -> sertarul se deschide singur, o dată pe job. Best effort: o problemă cu sertarul NU strică bonul."""
        try:
            if job.kind != "receipt" or not drawer.configured(self.cfg) or not drawer.settings(self.cfg)["auto_open"]:
                return
            if (self.store.get(job.external_id)["state"] or {}).get("drawer"):
                return   # deja încercat pentru acest job
            if not any(p.method == "cash" for p in job.receipt().payments):
                return
            ok, cod, mesaj = drawer.open_drawer(self.cfg)
            stare = dict(self.store.get(job.external_id)["state"] or {})
            stare["drawer"] = {"auto": True, "opened": ok, "error": None if ok else {"code": cod, "message": mesaj}}
            self.store.update(job.external_id, state=stare)
        except Exception:
            log.exception("deschiderea automată a sertarului a eșuat pentru %s", job.external_id)

    def _fail(self, job, error):
        status = "uncertain" if isinstance(error, Uncertain) else "failed"
        self.store.update(job.external_id, status=status, last_resolve=time.time(),
                          error=error.to_dict())


def public_job(row, driver_name, agent_id=None):
    """Forma JSON întoarsă browserului. `agent_id` = identitatea stabilă a acestui agent (acest calculator): backend-ul o leagă de job,
    ca un bon pornit aici să nu fie retrimis, la reluare, pe alt calculator (s-ar putea tipări de două ori)."""
    return {
        "ok": True,
        "agent_id": agent_id,
        "external_id": row["external_id"],
        "kind": row["kind"],
        "status": row["status"],           # received | running | printed | failed | uncertain
        "attempt": row["attempt"],
        "driver": driver_name,
        "fiscal_job_id": row["fiscal_job_id"],
        "result": row["result"],
        "error": row["error"],
        "pos": row["pos"],
        "alias_of": row.get("alias_of"),   # jobul care execută de fapt plata, dacă acesta e doar o oglindă (același payment_key)
        "drawer": (row.get("state") or {}).get("drawer"),   # deschiderea automată a sertarului: {auto, opened, error} sau None
    }
