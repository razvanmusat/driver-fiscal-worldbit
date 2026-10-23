"""Driver FisCool, scris pe documentația oficială: https://fiscool.ro/documentatie/ (api-http, operatiuni, coduri-eroare).

Ce garantează FisCool și folosim (documentat):
  - `Idempotency-Key`: toate cererile cu aceeași cheie partajează o singură execuție; duplicatele primesc același rezultat
    fără reexecuție. Deci un răspuns pierdut se lămurește retrimițând EXACT aceeași cerere cu aceeași cheie.
  - `vat` ca procent ("21%"): driverul FisCool îl mapează la grupa aparatului; eroare dacă nu există o grupă activă cu acea cotă.
  - `print_receipt` eșuează imediat cu 1004 dacă dispozitivul nu e în starea Connected.
  - orice răspuns non-200 e eșec; `status` din corp e codul de eroare al aplicației.
  - `fiscal_receipt_count` = contorul de bonuri fiscale al DISPOZITIVULUI (nu numărul din raportul Z) -> `receipt_in_z` rămâne None.
  - seria hardware vine din GET /devices/fiscal (`connected_device_id`), nu din răspunsul bonului.

Clasificarea erorilor (din pagina „Coduri Eroare” -> „Bune practici”):
  - tranzitorii cu reîncercare pe aceeași cheie: 408, 605, 606, 1005 -> `Uncertain` (se retrimite cu aceeași cheie);
  - dinainte de tipărire / stare / configurare, documentate: 1004, 505, 530, 531, 532, 544, 545, 400, 404, 540, 1009,
    POS 600-604 -> eșec cu retrimitere sigură;
  - orice altceva (hardware 500-519, 1000-1003, 1011 etc.): documentația nu garantează că nu s-a tipărit ->
    eșec cu `retry_safe=False`, reîncercarea cere confirmarea operatorului.

VERIFICAT pe FisCool real (casă simulată): FisCool memorează și ERORILE pe cheie („Lipsă hârtie", apoi hârtie pusă: aceeași cheie
întoarce aceeași eroare). De aceea după un răspuns de eroare reluarea folosește cheie nouă (`<id>-N`), iar o încercare rămasă FĂRĂ
răspuns (răspuns pierdut, termen depășit, agent repornit) se reia cu ACEEAȘI cheie: dacă bonul a ieșit totuși, FisCool îl recunoaște.
Cheile se țin doar în memoria FisCool (după repornirea lui aceeași cheie se execută din nou).

NEVERIFICAT (documentația nu spune; de confirmat pe un aparat real în perioada de test):
  - dacă și erorile 408/605/606/1005 se memorează pe cheie (nu s-a putut provoca pe simulator);
  - POS: dacă la dispozitiv e activ `auto_send_card_to_pos`, FisCool trimite singur suma cardului către POS-ul asociat
    înaintea bonului (documentat). Răspunsul bonului NU conține RRN/cod autorizare.
"""
from decimal import Decimal

from .. import httpc
from ..contract import percent_key
from ..errors import (NotConnected, NothingSent, PosDeclined, PrintFailed, Uncertain)
from . import catalog_fiscool
from .base import Field, FiscalDriver, FiscalHealth, FiscalResult

_SAFE_FAILED = {400, 404, 540, 1009, 505, 530, 531, 532, 544, 545}
_POS_DECLINED = {600, 601, 602, 603, 604}
_TRANSIENT = {408, 605, 606, 1005}


def _cents(amount):
    return int(amount * 100)


def _thousandths(quantity):
    return int(quantity * 1000)


def _s(value):
    return None if value is None else str(value)


class FiscoolDriver(FiscalDriver):
    name = "fiscool"
    files = ("drivers/fiscool.py", "drivers/catalog_fiscool.py")
    same_key_after_unknown = True
    fields = (
        Field("base_url", "Adresa FisCool API", "text", "http://127.0.0.1:10123"),
        Field("device_id", "ID dispozitiv fiscal (UUID)", "text", "",
              "UUID-ul de configurare (config.id). Butonul „Detectează dispozitive” îl afișează."),
        Field("vat_map", "Înlocuire TVA (opțional)", "map", {},
              "Implicit se trimite procentul (ex. 21%), iar FisCool alege grupa aparatului cu acea cotă. "
              "Doar dacă ai nevoie de altceva (ex. 21=A, litera grupei). Câte una pe linie."),
        Field("max_name_length", "Lungime maximă denumire produs", "int", 36,
              "Valoare prudentă; FisCool elimină oricum diacriticele. O denumire trunchiată e inofensivă."),
        Field("request_timeout_s", "Timeout cerere tipărire (secunde)", "int", 25,
              "Sub termenul total (config max_wait_s, implicit 30 s)."),
    )

    def __init__(self, settings):
        super().__init__(settings)
        self.base = settings["base_url"].rstrip("/")
        self.device_id = (settings.get("device_id") or "").strip()

    # ------------------------------------------------------------------ dispozitive
    def _fiscal_devices(self):
        status, data, _ = httpc.request("GET", self.base + "/devices/fiscal", timeout=3)
        if status != 200 or not isinstance(data, dict) or not isinstance(data.get("fiscal_devices"), list):
            raise httpc.NetworkError(f"răspuns neașteptat la lista de dispozitive (HTTP {status})")
        return data["fiscal_devices"]

    def _my_device(self):
        for entry in self._fiscal_devices():
            if isinstance(entry, dict) and (entry.get("config") or {}).get("id") == self.device_id:
                return entry
        return None

    def health(self):
        if not self.device_id:
            return FiscalHealth(False, "ID-ul dispozitivului fiscal nu e setat în /setari")
        try:
            entry = self._my_device()
        except httpc.HttpError as e:
            return FiscalHealth(False, f"FisCool nu răspunde: {e}", transient=True)
        if entry is None:
            return FiscalHealth(False, "Dispozitivul setat nu apare în FisCool")
        if entry.get("status") != "Connected":
            return FiscalHealth(False, f"Casa de marcat: {entry.get('status_text') or entry.get('status')}", {
                "driver_name": str(entry.get("status")), "driver_response": entry},
                transient=entry.get("status") == "Connecting")   # documentat: Disconnected | Connecting | Connected | Disabled | Error
        return FiscalHealth(True, "", {"driver_response": entry})

    def discover(self):
        """Pentru /setari: dispozitivele fiscale din FisCool, ca adminul să-și copieze ID-ul."""
        try:
            devices = self._fiscal_devices()
        except httpc.HttpError as e:
            return f"FisCool nu răspunde: {e}"
        if not devices:
            return "FisCool nu are niciun dispozitiv fiscal configurat."
        lines = []
        for d in devices:
            cfg = d.get("config") or {}
            lines.append(f"{cfg.get('name')} | id: {cfg.get('id')} | stare: {d.get('status_text') or d.get('status')} | "
                         f"serie: {d.get('connected_device_id')} | card către POS automat: "
                         f"{cfg.get('auto_send_card_to_pos')}")
        return "\n".join(lines)

    def vat_groups(self):
        """Pentru /setari: grupele de TVA pe care FisCool le citește de pe aparat (`vat_rates` din GET /devices/fiscal: litera grupei -> cota în sutimi de
        procent, ex. "A": 1900 = 19%), ca adminul să aleagă una. Întoarce [{"letter": "A", "percent": "19"}, ...]; ridică ValueError cu mesajul pentru admin."""
        if not self.device_id:
            raise ValueError("ID-ul dispozitivului fiscal nu e setat: completează-l și salvează setările")
        try:
            entry = self._my_device()
        except httpc.HttpError as e:
            raise ValueError(f"FisCool nu răspunde: {e}")
        if entry is None:
            raise ValueError("Dispozitivul setat nu apare în FisCool")
        rates = entry.get("vat_rates")
        if not isinstance(rates, dict) or not rates:
            raise ValueError("FisCool nu a întors grupele de TVA ale aparatului (casa de marcat e conectată?)")
        groups = []
        for letter, value in sorted(rates.items()):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            groups.append({"letter": str(letter), "percent": percent_key(Decimal(str(value)) / 100)})
        if not groups:
            raise ValueError("FisCool nu a întors nicio cotă de TVA citibilă")
        return groups

    def _serial(self):
        """Seria hardware, best-effort: nu poate strica un bon deja tipărit."""
        try:
            entry = self._my_device()
        except httpc.HttpError:
            return None
        return _s(entry.get("connected_device_id")) if entry else None

    # ------------------------------------------------------------------ cereri
    def _name(self, raw):
        name = " ".join(raw.split())
        return (name or "Produs")[: int(self.settings["max_name_length"])].rstrip()

    def _vat(self, percent):
        key = percent_key(percent)
        mapped = self.settings["vat_map"].get(key)
        return str(mapped) if mapped else f"{key}%"

    def receipt_operation(self, receipt):
        operation = {
            "type": "print_receipt",
            "lines": [{
                "type": "item",
                "description": self._name(l.name),
                "quantity_thousandths": _thousandths(l.quantity),
                "unit_price_cents": _cents(l.unit_price),
                "vat": self._vat(l.vat_percent),
                "um": l.unit,
            } for l in receipt.lines],
            "payments": [{"method": p.method, "amount_cents": _cents(p.amount)} for p in receipt.payments],
            "close_action": "close",
        }
        if receipt.buyer_cui:
            operation["buyer"] = {"vat_number": receipt.buyer_cui}
        return operation

    def print_receipt(self, receipt, job):
        return self._start(job, self.receipt_operation(receipt), "receipt")

    def print_report(self, kind, job):
        return self._start(job, {"type": "print_x" if kind == "X" else "print_z"}, "report")

    def _start(self, job, operation, kind):
        if not self.device_id:
            raise NotConnected("ID-ul dispozitivului fiscal nu e setat în /setari", code="DEVICE_NOT_SET")
        job.state["request"] = {"device_id": self.device_id, "operation": operation}
        job.state["kind"] = kind
        job.save()   # corpul exact se păstrează: după un răspuns pierdut se retrimite cu ACEEAȘI cheie
        return self._send(job)

    def resolve(self, job):
        if "request" not in job.state:
            raise NothingSent("Comanda nu a fost trimisă către FisCool")
        return self._send(job)

    def _send(self, job):
        timeout = float(self.settings["request_timeout_s"])
        try:
            status, data, _ = httpc.request(
                "POST", self.base + "/devices/operation", job.state["request"],
                headers={"Idempotency-Key": job.key}, timeout=timeout)
        except httpc.Refused:
            raise NotConnected("FisCool a refuzat conexiunea; nimic nu a fost trimis", transient=True)
        except httpc.HttpError:
            raise Uncertain("Răspunsul FisCool s-a pierdut; se retrimite cu aceeași cheie de idempotență",
                            code="LOST_RESPONSE")
        if not isinstance(data, dict):
            raise Uncertain("Răspuns necitibil de la FisCool", code="LOST_RESPONSE")
        if status == 200 and data.get("status") == 0:
            return self._result(job, data)
        raise self._error(status, data)

    def _result(self, job, data):
        if job.state.get("kind") == "report":
            return FiscalResult(report_number=_s(data.get("report_number")), device_serial=self._serial(),
                                message=_s(data.get("msg")), raw=data)
        return FiscalResult(
            slip_number=_s(data.get("slip_number")),
            z_report_number=_s(data.get("z_report_number")),
            receipt_in_z=None,     # fiscal_receipt_count e contorul dispozitivului, nu numărul din raportul Z
            device_serial=self._serial(),
            message=_s(data.get("msg")),
            raw=data,              # răspunsul complet: msg, slip_number, z_report_number, fiscal_receipt_count, bon_id
        )

    @staticmethod
    def _error(http_status, data):
        code = data.get("status")
        message = f"{data.get('error') or data.get('msg') or 'eroare necunoscută'} (FisCool {code}, HTTP {http_status})"
        tag = f"FISCOOL_{code}"
        # tot ce a spus FisCool: codul, numele/descrierea/rezolvarea documentate, chitanța de refuz POS (`receipt`) și răspunsul brut
        documentat = catalog_fiscool.descrie(code)
        extra = dict(
            driver_code=str(code),
            driver_name=documentat[0] if documentat else None,
            driver_description=documentat[1] if documentat else None,
            resolution=documentat[2] if documentat else None,
            receipt=data.get("receipt"),
            driver_response=dict(data, http_status=http_status),
        )
        # 1004 și 505 sunt în lista „erori tranzitorii” din pagina Coduri Eroare (Logica de reîncercare) și nu au executat nimic
        if code == 1004:
            return NotConnected(message, code=tag, transient=True, **extra)
        if code in _POS_DECLINED:
            return PosDeclined(message, code=tag, **extra)
        if code in _SAFE_FAILED:
            return PrintFailed(message, code=tag, retry_safe=True, transient=(code == 505), **extra)
        if code in _TRANSIENT:
            return Uncertain(message, code=tag, **extra)
        return PrintFailed(message, code=tag, retry_safe=False, **extra)
