"""Interfața POS bancar. ÎN AȘTEPTARE: modulele concrete (Ingenico/PAX/Verifone etc.) se scriu după ce
banca livrează protocolul ECR/POS (PDF-ul cu comenzile). Până atunci modelul „none” înseamnă că agentul
NU apelează POS-ul, iar suma de card se presupune încasată în afara agentului (ca azi).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from ..errors import Uncertain


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
    fields = ()

    def __init__(self, settings):
        self.settings = settings

    @abstractmethod
    def sale(self, amount, job) -> PosResult:
        """Cere plata. Ridică PosDeclined dacă e respinsă, Uncertain dacă nu se știe dacă s-a încasat."""

    def resolve(self, job) -> PosResult:
        """Lămurește o plată rămasă incertă. Implicit rămâne incertă."""
        raise Uncertain("Starea plății cu cardul nu poate fi confirmată automat", code="POS_UNCERTAIN")


POS_DRIVERS = {}   # se populează când sosește protocolul băncii


def build_pos(cfg):
    model = cfg["pos"]["model"]
    if model == "none":
        return None
    if model not in POS_DRIVERS:
        raise ValueError(f"model POS necunoscut: {model}")
    return POS_DRIVERS[model](cfg["pos"].get("settings", {}))
