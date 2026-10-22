"""Interfața POS bancar. Modelul „none” înseamnă că agentul NU apelează POS-ul, iar suma de card se presupune încasată în afara agentului.
Un model nou = un fișier nou în pos/, înregistrat în pos/__init__.py."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from ..errors import DriverError, Uncertain


@dataclass
class PosResult:
    status: str                          # "approved"
    transaction_id: Optional[str] = None
    auth_code: Optional[str] = None
    rrn: Optional[str] = None
    card_masked: Optional[str] = None
    raw: dict = field(default_factory=dict)

    def to_dict(self):
        return {"status": self.status, "transaction_id": self.transaction_id, "auth_code": self.auth_code,
                "rrn": self.rrn, "card_masked": self.card_masked, "raw": self.raw}


class PosDriver(ABC):
    name = ""
    label = ""      # numele din lista de modele din /setari
    bank = ""       # lista din /setari e grupată pe bancă (aplicația băncii din terminal stabilește protocolul, nu marca terminalului)
    models = ()     # terminalele pe care protocolul a fost verificat; se adaugă pe măsură ce se testează
    fields = ()     # câmpurile de setări ale modelului (drivers.base.Field)
    files = ()      # fișierele modelului din pachet, relative la worldbit_fiscal/ (verificarea de actualizare din /setari)

    def __init__(self, settings):
        self.settings = settings

    @classmethod
    def validate_settings(cls, settings):
        """Ridică ValueError cu mesajul pentru admin dacă setările nu sunt bune."""

    @abstractmethod
    def sale(self, amount, job) -> PosResult:
        """Cere plata. Ridică PosDeclined dacă e respinsă, Uncertain dacă nu se știe dacă s-a încasat."""

    def void(self, invoice, job):
        """Anulează o plată aprobată (numărul chitanței POS). Cu succes `job.pos` devine „voided”; ridică DriverError dacă nu se poate."""
        raise DriverError("Modelul de POS nu poate anula plăți", code="POS_UNSUPPORTED", retry_safe=False)

    def settlement(self):
        """Decontarea de la sfârșit de zi (la modelele care o au). True dacă terminalul a primit comanda; nu ridică erori."""
        return False

    def resolve(self, job) -> PosResult:
        """Lămurește o plată rămasă incertă. Implicit rămâne incertă."""
        raise Uncertain("Starea plății cu cardul nu poate fi confirmată automat", code="POS_UNCERTAIN")


POS_DRIVERS = {}   # modelele se înregistrează singure, din pos/__init__.py


def build_pos(cfg):
    model = cfg["pos"]["model"]
    if model == "none":
        return None
    if model not in POS_DRIVERS:
        raise ValueError(f"model POS necunoscut: {model}")
    cls = POS_DRIVERS[model]
    settings = {f.key: f.default for f in cls.fields}
    settings.update(cfg["pos"].get("settings") or {})
    return cls(settings)

