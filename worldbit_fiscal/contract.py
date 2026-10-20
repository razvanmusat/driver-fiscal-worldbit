"""Contractul universal: JSON-ul pe care aplicația web îl trimite agentului, indiferent de driver.

Sume și cantități sunt zecimale (nu bani întregi); conversia în formatul fiecărui driver o face driverul.
TVA-ul vine ca procent; maparea pe indexul/litera aparatului e în setările agentului.
"""
import json
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

CENT = Decimal("0.01")
_EXTERNAL_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
METHODS = ("cash", "card")


class InvalidPayload(Exception):
    pass


@dataclass(frozen=True)
class Line:
    name: str
    unit_price: Decimal
    quantity: Decimal
    unit: str
    vat_percent: Decimal

    @property
    def total(self) -> Decimal:
        return (self.unit_price * self.quantity).quantize(CENT, ROUND_HALF_UP)


@dataclass(frozen=True)
class Payment:
    method: str
    amount: Decimal


@dataclass(frozen=True)
class Receipt:
    external_id: str
    lines: List[Line]
    payments: List[Payment]
    total: Decimal
    buyer_cui: Optional[str] = None
    # Identitatea PLĂȚII (stabilă între joburile aceluiași bon): un job nou cu același payment_key nu mai tipărește, îl urmează pe primul.
    payment_key: Optional[str] = None

    @property
    def card_total(self) -> Decimal:
        return sum((p.amount for p in self.payments if p.method == "card"), Decimal("0"))

    def canonical(self) -> str:
        """Formă stabilă pentru a recunoaște aceeași cerere retrimisă cu același external_id."""
        return json.dumps({"external_id": self.external_id, "payment_key": self.payment_key, **json.loads(self.content())},
                          ensure_ascii=False, sort_keys=True)

    def content(self) -> str:
        """Conținutul bonului, fără identitățile jobului (external_id, payment_key): două joburi cu aceeași plată trebuie să aibă același conținut."""
        return json.dumps({
            "lines": [[l.name, str(l.unit_price), str(l.quantity), l.unit, str(l.vat_percent)] for l in self.lines],
            "payments": [[p.method, str(p.amount)] for p in self.payments],
            "total": str(self.total),
            "buyer_cui": self.buyer_cui,
        }, ensure_ascii=False, sort_keys=True)


def _decimal(value, field, places):
    if isinstance(value, bool) or not isinstance(value, (int, str, Decimal)):
        raise InvalidPayload(f"{field}: valoare numerică invalidă")
    try:
        d = Decimal(str(value))
    except Exception:
        raise InvalidPayload(f"{field}: valoare numerică invalidă")
    if not d.is_finite():
        raise InvalidPayload(f"{field}: valoare numerică invalidă")
    if d != d.quantize(Decimal(1).scaleb(-places)):
        raise InvalidPayload(f"{field}: maximum {places} zecimale")
    return d


def validate_external_id(value):
    if not isinstance(value, str) or not _EXTERNAL_ID.match(value):
        raise InvalidPayload("external_id: 1-64 caractere din [A-Za-z0-9._-]")
    return value


def validate_payment_key(value):
    if value is None:
        return None
    if not isinstance(value, str) or not _EXTERNAL_ID.match(value):
        raise InvalidPayload("payment_key: 1-64 caractere din [A-Za-z0-9._-]")
    return value


def parse_receipt(data) -> Receipt:
    """`data` = JSON-ul parsat cu parse_float=Decimal. Ridică InvalidPayload înainte de orice contact cu aparatul."""
    if not isinstance(data, dict):
        raise InvalidPayload("corpul trebuie să fie un obiect JSON")
    external_id = validate_external_id(data.get("external_id"))

    raw_lines = data.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise InvalidPayload("lines: cel puțin o linie")
    lines = []
    for i, raw in enumerate(raw_lines, 1):
        if not isinstance(raw, dict):
            raise InvalidPayload(f"lines[{i}]: obiect așteptat")
        name = raw.get("name")
        if not isinstance(name, str) or not _CONTROL.sub(" ", name).strip():
            raise InvalidPayload(f"lines[{i}].name: obligatoriu")
        unit = raw.get("unit", "buc")
        if not isinstance(unit, str) or not 1 <= len(unit.strip()) <= 10:
            raise InvalidPayload(f"lines[{i}].unit: 1-10 caractere")
        price = _decimal(raw.get("unit_price"), f"lines[{i}].unit_price", 2)
        qty = _decimal(raw.get("quantity"), f"lines[{i}].quantity", 3)
        vat = _decimal(raw.get("vat_percent"), f"lines[{i}].vat_percent", 2)
        if price <= 0:
            raise InvalidPayload(f"lines[{i}].unit_price: trebuie să fie > 0")
        if qty <= 0:
            raise InvalidPayload(f"lines[{i}].quantity: trebuie să fie > 0")
        if not 0 <= vat <= 100:
            raise InvalidPayload(f"lines[{i}].vat_percent: între 0 și 100")
        lines.append(Line(_CONTROL.sub(" ", name).strip(), price, qty, unit.strip(), vat))

    raw_payments = data.get("payments")
    if not isinstance(raw_payments, list) or not raw_payments:
        raise InvalidPayload("payments: cel puțin o plată")
    payments, seen = [], set()
    for i, raw in enumerate(raw_payments, 1):
        if not isinstance(raw, dict):
            raise InvalidPayload(f"payments[{i}]: obiect așteptat")
        method = raw.get("method")
        if method not in METHODS:
            raise InvalidPayload(f"payments[{i}].method: {' sau '.join(METHODS)}")
        if method in seen:
            raise InvalidPayload(f"payments[{i}].method: '{method}' apare de două ori")
        seen.add(method)
        amount = _decimal(raw.get("amount"), f"payments[{i}].amount", 2)
        if amount <= 0:
            raise InvalidPayload(f"payments[{i}].amount: trebuie să fie > 0")
        payments.append(Payment(method, amount))

    total = _decimal(data.get("total"), "total", 2)
    lines_total = sum((l.total for l in lines), Decimal("0"))
    payments_total = sum((p.amount for p in payments), Decimal("0"))
    if lines_total != total:
        raise InvalidPayload(f"total: {total} nu coincide cu suma liniilor ({lines_total})")
    if payments_total != total:
        raise InvalidPayload(f"total: {total} nu coincide cu suma plăților ({payments_total})")

    buyer_cui = None
    buyer = data.get("buyer")
    if buyer is not None:
        if not isinstance(buyer, dict):
            raise InvalidPayload("buyer: obiect așteptat")
        cui = buyer.get("cui")
        if cui is not None:
            if not isinstance(cui, str) or not re.fullmatch(r"(RO)?[0-9]{2,10}", cui.strip()):
                raise InvalidPayload("buyer.cui: format invalid")
            buyer_cui = cui.strip()

    return Receipt(external_id, lines, payments, total, buyer_cui, validate_payment_key(data.get("payment_key")))


def parse_json(text: str):
    """JSON cu zecimale exacte (Decimal), nu float."""
    try:
        return json.loads(text, parse_float=Decimal)
    except ValueError:
        raise InvalidPayload("JSON invalid")


def percent_key(value: Decimal) -> str:
    """Cheia TVA din setări: 21.00 -> '21', 9.5 -> '9.5'."""
    return format(value.normalize(), "f")
