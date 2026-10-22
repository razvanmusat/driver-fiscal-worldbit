"""Interfața driverelor fiscale. Fiecare driver își declară singur setările (afișate în /setari doar pentru driverul ales)."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str          # text | int | float | map
    default: object
    help: str = ""


@dataclass
class FiscalHealth:
    ready: bool
    detail: str = ""
    # tot ce a spus driverul despre stare (codul/numele, descrierea și rezolvarea documentate, răspunsul brut) — se transportă până în Dressbit
    extra: dict = field(default_factory=dict)
    # starea „nu e pregătită” e trecătoare (documentat de driver): agentul reîncearcă singur
    transient: bool = False


@dataclass
class FiscalResult:
    slip_number: Optional[str] = None       # numărul general al bonului (Fisco: SlipNumber)
    z_report_number: Optional[str] = None   # raportul Z care conține bonul (Fisco: nZrep)
    receipt_in_z: Optional[str] = None      # numărul bonului în raportul Z (Fisco: nFNum)
    device_serial: Optional[str] = None
    report_number: Optional[str] = None     # la rapoarte X/Z (Fisco: nRep)
    message: Optional[str] = None           # mesajul de succes al driverului (FisCool: `msg`, ex. „Receipt printed”)
    raw: dict = field(default_factory=dict)  # răspunsul COMPLET al driverului, neschimbat

    def to_dict(self):
        return {
            "slip_number": self.slip_number,
            "z_report_number": self.z_report_number,
            "receipt_in_z": self.receipt_in_z,
            "device_serial": self.device_serial,
            "report_number": self.report_number,
            "message": self.message,
            "raw": self.raw,
        }


class FiscalDriver(ABC):
    name = ""
    fields = ()
    files = ()   # fișierele driverului din pachet, relative la worldbit_fiscal/ (verificarea de actualizare din /setari)
    # Driverul recunoaște o cheie deja executată (idempotență): o încercare rămasă fără răspuns se reia cu aceeași cheie.
    same_key_after_unknown = False

    def __init__(self, settings):
        self.settings = settings

    @abstractmethod
    def health(self) -> FiscalHealth:
        """Nu ridică excepții: întoarce ready=False cu motivul."""

    @abstractmethod
    def print_receipt(self, receipt, job) -> FiscalResult:
        """Tipărește bonul. Ridică erorile din `errors`. `job` oferă `key`, `state`, `save()`, `set_fiscal_job_id()`."""

    @abstractmethod
    def print_report(self, kind, job) -> FiscalResult:
        """kind = 'X' sau 'Z'."""

    @abstractmethod
    def resolve(self, job) -> FiscalResult:
        """Lămurește un job rămas incert. Întoarce rezultatul dacă s-a tipărit; altfel ridică
        PrintFailed / NothingSent (dovadă negativă) sau Uncertain (încă nu se poate spune)."""
