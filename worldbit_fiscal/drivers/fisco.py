"""Driver Fisco (https://fisco.ro/manual-fisco/), portat din logica Sellbit (FiscalAgentService + ReceiptFiscalService).

Pași identici cu Sellbit: snapshot al ultimului job ÎNAINTE de POST, apoi POST /api/v1/print, apoi polling pe job_id.
La răspuns pierdut, reconcilierea compară joburile Fisco cu snapshotul. Nu se retrimite niciodată un job neconfirmat.

IMPORTANT: manualul Fisco NU documentează nicio idempotență pe `external_id` (apare doar în exemplul de cerere) și
recomandă explicit „evitați retrimiterea automată a aceluiași bon fără verificarea statusului". De aceea protecția
împotriva bonului dublu rămâne aici, în agent (snapshot + reconciliere), nu în driverul Fisco.

Formatul FPrint (verificat în manual, secțiunile 5.1, 5.2, 5.6): linie produs
S,1,______,_,__;<Denumire max 72>;<Preț>;<Cantitate>;<Departament>;1;<Cotă TVA>;0;0;<UM max 6>
unde <Cotă TVA> e indexul 1-5 SAU procentul cu semnul % (Fisco caută prima cotă configurată cu acel procent și dă
eroare dacă nu există); plată T,1,______,_,__;<0 numerar | 1 card>;<Sumă>;;;; ; CUI client K,1,______,_,__;<CUI>; înaintea
primului produs.
"""
import re
import time
from urllib.parse import quote

from .. import httpc
from ..contract import percent_key
from ..errors import NotConnected, NothingSent, PrintFailed, Rejected, Uncertain
from . import catalog_fisco
from .base import Field, FiscalDriver, FiscalHealth, FiscalResult

SNAPSHOT_EMPTY = "NONE"   # istoricul Fisco era gol la snapshot (diferit de „niciun snapshot”)
_SEPARATORS = re.compile(r"[;^\x00-\x1f]")


def _s(value):
    return None if value is None else str(value)


def _cod_eroare(data):
    err = data.get("error") if isinstance(data, dict) else None
    return err.get("code") if isinstance(err, dict) and err.get("code") else None


def _extra(cod, mesaj, raspuns):
    """Tot ce a spus Fisco despre o eroare: codul API, descrierea lui din manual, rezolvarea documentată (dacă există) și răspunsul brut."""
    return dict(driver_code=cod, driver_name=cod, driver_description=catalog_fisco.descriere_cod(cod),
                resolution=catalog_fisco.rezolvare(mesaj), driver_response=raspuns)


class FiscoDriver(FiscalDriver):
    name = "fisco"
    files = ("drivers/fisco.py", "drivers/catalog_fisco.py")
    fields = (
        Field("base_url", "Adresa Fisco", "text", "http://127.0.0.1:4040"),
        Field("vat_map", "Înlocuire TVA (opțional)", "map", {},
              "Implicit se trimite procentul (ex. 21%), iar Fisco alege cota cu acel procent din casă. "
              "Doar dacă ai nevoie de altceva (ex. 21=5 pentru neplătitor TVA, index 1-5). Câte una pe linie."),
        Field("max_name_length", "Lungime maximă denumire produs", "int", 72),
        Field("poll_timeout_s", "Așteptare tipărire (secunde)", "int", 25,
              "Sub termenul total (config max_wait_s, implicit 30 s), ca agentul să apuce să lămurească jobul înainte de abandon."),
        Field("poll_interval_s", "Interval verificare status (secunde)", "float", 1.0),
    )

    def __init__(self, settings):
        super().__init__(settings)
        self.base = settings["base_url"].rstrip("/")

    # ------------------------------------------------------------------ health
    def health(self):
        try:
            status, data, _ = httpc.request("GET", self.base + "/api/v1/health", timeout=3)
        except httpc.HttpError as e:
            return FiscalHealth(False, f"Fisco nu răspunde: {e}", transient=True)
        if status != 200 or not isinstance(data, dict):
            return FiscalHealth(False, f"Fisco a răspuns HTTP {status}", transient=True)
        printer = data.get("printer")
        if isinstance(printer, dict) and printer.get("ready") is True:
            return FiscalHealth(True, "", {"driver_response": data})
        detail = printer.get("error") if isinstance(printer, dict) else ""
        return FiscalHealth(False, detail or "Imprimanta fiscală nu e pregătită", {
            "driver_name": printer.get("status") if isinstance(printer, dict) else None,
            "driver_description": catalog_fisco.COD_API["PRINTER_NOT_READY"],
            "resolution": catalog_fisco.rezolvare(detail),
            "driver_response": data},
            # „Printer status is not known yet” e unul dintre motivele documentate de manual și trece singur;
            # depășirea celor 24h sau procesarea oprită cer o acțiune (raport Z / pornirea procesării), nu se rezolvă prin așteptare
            transient="Printer status is not known yet" in (detail or ""))

    # ------------------------------------------------------------------ comenzi
    def _name(self, raw):
        name = " ".join(_SEPARATORS.sub(" ", raw).split())
        return (name or "Produs")[: int(self.settings["max_name_length"])].rstrip()

    def _vat(self, percent):
        key = percent_key(percent)
        mapped = self.settings["vat_map"].get(key)
        return str(mapped) if mapped else f"{key}%"

    def receipt_commands(self, receipt):
        commands = []
        if receipt.buyer_cui:
            commands.append("K,1,______,_,__;%s;" % receipt.buyer_cui)
        for line in receipt.lines:
            commands.append("S,1,______,_,__;%s;%.2f;%.3f;1;1;%s;0;0;%s" % (
                self._name(line.name), line.unit_price, line.quantity, self._vat(line.vat_percent), line.unit[:6]))
        for payment in receipt.payments:
            ptype = 0 if payment.method == "cash" else 1
            commands.append("T,1,______,_,__;%d;%.2f;;;;" % (ptype, payment.amount))
        return commands

    # ------------------------------------------------------------------ operații publice
    def print_receipt(self, receipt, job):
        commands = self.receipt_commands(receipt)   # eșec de configurare = înainte de orice contact cu aparatul
        return self._submit("fprint", commands, job)

    def print_report(self, kind, job):
        return self._submit("simplu", ["X^" if kind == "X" else "Z^"], job)

    def _submit(self, syntax, commands, job):
        latest = self._latest_job_id()   # NotConnected dacă Fisco nu răspunde: nimic trimis încă
        job.state["snapshot"] = latest or SNAPSHOT_EMPTY
        job.save()                       # persistat ÎNAINTE de POST

        body = {"syntax": syntax, "version": "1.0", "external_id": job.key, "commands": commands}
        try:
            _, data, _ = httpc.request("POST", self.base + "/api/v1/print", body, timeout=10)
        except httpc.Refused:
            raise NotConnected("Fisco a refuzat conexiunea; nimic nu a fost trimis", transient=True)
        except httpc.HttpError:
            raise Uncertain("Răspunsul la comanda de tipărire s-a pierdut; se verifică în Fisco", code="LOST_RESPONSE")
        if not isinstance(data, dict):
            raise Uncertain("Răspuns necitibil de la Fisco; se verifică în Fisco", code="LOST_RESPONSE")
        if data.get("ok") is not True:
            err = data.get("error")
            mesaj = err.get("message") if isinstance(err, dict) and err.get("message") else "Comandă respinsă"
            raise Rejected(mesaj, **_extra(_cod_eroare(data), mesaj, data))
        job_id = data.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise Uncertain("Fisco a acceptat comanda fără job_id; se verifică în Fisco", code="LOST_RESPONSE")
        job.set_fiscal_job_id(job_id)    # reținut imediat, înainte de polling
        return self._poll(job_id)

    # ------------------------------------------------------------------ status
    def _latest_job_id(self):
        jobs = self._job_ids("/api/v1/status?limit=1")
        return jobs[0] if jobs else None

    def _job_ids(self, path):
        try:
            status, data, _ = httpc.request("GET", self.base + path, timeout=10)
        except httpc.HttpError:
            raise NotConnected("Fisco nu răspunde", transient=True)
        if not isinstance(data, dict) or not isinstance(data.get("jobs", []), list):
            raise NotConnected("Fisco a dat un răspuns necitibil la lista de joburi", transient=True)
        return [j["job_id"] for j in data.get("jobs", []) if isinstance(j, dict) and isinstance(j.get("job_id"), str)]

    def _poll(self, job_id):
        deadline = time.monotonic() + float(self.settings["poll_timeout_s"])
        interval = float(self.settings["poll_interval_s"])
        while True:
            time.sleep(interval)
            try:
                _, data, _ = httpc.request("GET", f"{self.base}/api/v1/status?job_id={quote(job_id)}", timeout=5)
            except httpc.HttpError:
                data = None   # un singur eșec de rețea nu înseamnă job pierdut; continuăm până la deadline
            if isinstance(data, dict):
                status = data.get("status")
                if status == "printed":
                    return self._result(data)
                if status == "failed":
                    mesaj = self._error_message(data, "Tipărirea a eșuat")
                    raise PrintFailed(mesaj, **_extra(_cod_eroare(data), mesaj, data))
            if time.monotonic() > deadline:
                raise Uncertain("Fisco nu a confirmat tipărirea în timp util; se verifică automat", code="TIMEOUT")

    @staticmethod
    def _error_message(data, default):
        err = data.get("error")
        return err.get("message") if isinstance(err, dict) and err.get("message") else default

    @staticmethod
    def _result(data):
        res = data.get("result")
        res = res if isinstance(res, dict) else {}
        return FiscalResult(
            slip_number=_s(res.get("SlipNumber")),
            z_report_number=_s(res.get("nZrep")),
            receipt_in_z=_s(res.get("nFNum")),
            device_serial=_s(res.get("DeviceSerial")),
            report_number=_s(res.get("nRep")),
            raw=data,
        )

    # ------------------------------------------------------------------ recuperare
    def resolve(self, job):
        job_id = job.fiscal_job_id
        if not job_id:
            snapshot = job.state.get("snapshot")
            if snapshot is None:
                raise NothingSent("Comanda nu a fost trimisă către Fisco")
            job_id = self._reconcile(job, snapshot)
            job.set_fiscal_job_id(job_id)

        try:
            _, data, _ = httpc.request("GET", f"{self.base}/api/v1/status?job_id={quote(job_id)}", timeout=10)
        except httpc.HttpError:
            raise Uncertain("Fisco nu răspunde acum; nu se poate confirma", code="UNCONFIRMED")
        if not isinstance(data, dict):
            raise Uncertain("Răspuns necitibil de la Fisco; nu se poate confirma", code="UNCONFIRMED")
        status = data.get("status")
        if status == "printed":
            return self._result(data)
        if status in ("queued", "processing"):
            raise Uncertain("Fisco încă procesează bonul", code="STILL_PROCESSING")
        if status is None or status in ("failed", "not_found"):
            # job_id confirmat acceptat + trimiteri serializate de agent = „not_found” înseamnă că nu mai poate fi tipărit
            mesaj = self._error_message(data, "Fisco nu mai are jobul")
            raise PrintFailed(mesaj, **_extra(_cod_eroare(data), mesaj, data))
        raise Uncertain(f"Status necunoscut de la Fisco: {status}", code="UNCONFIRMED")

    def _reconcile(self, job, snapshot):
        """Răspuns pierdut la POST: exact un job nou necunoscut = al nostru; zero = Fisco n-a primit nimic."""
        try:
            jobs = self._job_ids("/api/v1/status")   # cele mai noi primele
        except NotConnected:
            raise Uncertain("Fisco nu răspunde acum; nu se poate confirma", code="UNCONFIRMED")
        if snapshot == SNAPSHOT_EMPTY:
            newer = jobs
        else:
            idx = jobs.index(snapshot) if snapshot in jobs else -1
            newer = jobs[:idx] if idx >= 0 else jobs   # snapshot absent = Fisco și-a pierdut istoricul
        candidates = [j for j in newer if not job.job_id_in_use(j)]
        if not candidates:
            raise NothingSent("Fisco nu a primit comanda")
        if len(candidates) == 1:
            return candidates[0]
        raise Uncertain("Mai multe joburi noi în Fisco; nu se poate decide automat", code="AMBIGUOUS")
