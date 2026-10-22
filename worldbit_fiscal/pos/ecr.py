"""Protocolul ECR Hypercom T7/FIP11 v1.06 („MOL”): cadre, LRC, mesajele 10/11/12/13. Fără intrare/ieșire: doar octeți și text.

Cadru: STX + bloc (max 128, ASCII) + ETX + LRC. LRC = XOR peste toți octeții de după STX, inclusiv ETX. Confirmări de transmisie: ACK (06) / NAK (15).
Mesaj: 3 litere (MOL = plată, VOI = anulare, STL = decontare, DIS = reducere) + 2 cifre (10 pornește plata, 11 „am început”, 12 rezultatul,
13 „am primit rezultatul”) + `.` + datele. Toți octeții se compară pe 7 biți (bitul 8 = paritate, dacă portul îl lasă să treacă)."""
from decimal import Decimal, ROUND_HALF_UP

STX, ETX, EOT, ACK, NAK, FS = 0x02, 0x03, 0x04, 0x06, 0x15, 0x1C
MAX_BLOCK = 128

# Răspunsurile (12) cu alt cod decât „00” sunt respingeri; documentul trimite la ISO-8583. Mesaje pentru cele uzuale; restul: mesaj general + cod.
RESPONSE_MESSAGES = {
    "05": "Tranzacție refuzată de bancă",
    "12": "Tranzacție invalidă",
    "14": "Număr de card invalid",
    "41": "Card blocat",
    "43": "Card blocat",
    "51": "Fonduri insuficiente",
    "54": "Card expirat",
    "55": "PIN incorect",
    "57": "Tranzacție nepermisă pentru acest card",
    "61": "Limita de sumă depășită",
    "62": "Card cu restricții",
    "65": "Limita de tranzacții depășită",
    "75": "Prea multe încercări de PIN",
    "91": "Banca emitentă nu răspunde",
    "96": "Eroare de sistem la bancă",
}


def lrc(block):
    """`block` = octeții de după STX, până la ETX inclusiv."""
    x = 0
    for b in block:
        x ^= b & 0x7F
    return x


def frame(body):
    """Cadrul gata de trimis pentru un mesaj (text ASCII, fără STX/ETX)."""
    data = body.encode("ascii")
    if len(data) > MAX_BLOCK:
        raise ValueError("mesaj prea lung (max 128)")
    block = data + bytes([ETX])
    return bytes([STX]) + block + bytes([lrc(block)])


class Reader:
    """Descompune fluxul primit de pe port în evenimente: ("ack",), ("nak",), ("frame", text, lrc_ok). Zgomotul dintre cadre se ignoră;
    octeții pot veni în bucăți oricât de mici."""

    def __init__(self):
        self._state = "idle"
        self._body = bytearray()

    def feed(self, data):
        events = []
        for raw in data:
            b = raw & 0x7F
            if self._state == "idle":
                if b == STX:
                    self._state, self._body = "body", bytearray()
                elif b == ACK:
                    events.append(("ack",))
                elif b == NAK:
                    events.append(("nak",))
            elif self._state == "body":
                if b == STX:                        # început nou de cadru: îl reia pe acesta
                    self._body = bytearray()
                elif b == ETX:
                    self._state = "lrc"
                else:
                    self._body.append(b)
                    if len(self._body) > MAX_BLOCK:
                        self._state = "idle"
            else:                                   # octetul LRC
                calculat = lrc(bytes(self._body) + bytes([ETX]))
                events.append(("frame", bytes(self._body).decode("ascii", "replace"), calculat == b))
                self._state = "idle"
        return events


def message(msg_id, msg_type, data=""):
    return f"{msg_id}{msg_type}.{data}"


def parse_message(body):
    """(id, tip, date) sau None dacă nu arată ca un mesaj ECR."""
    if len(body) < 6 or body[5] != "." or not body[:3].isalpha() or not body[3:5].isdigit():
        return None
    return body[:3], body[3:5], body[6:]


def amount12(amount):
    """Suma pe 12 cifre, fără virgulă (100,00 lei -> 000000010000)."""
    cents = int((Decimal(amount) * 100).to_integral_value(ROUND_HALF_UP))
    if Decimal(cents) != Decimal(amount) * 100:
        raise ValueError("suma are mai mult de 2 zecimale")
    if not 0 < cents < 10 ** 12:
        raise ValueError("suma în afara intervalului")
    return str(cents).zfill(12)


def mask_pan(pan):
    pan = (pan or "").strip()
    if not pan:
        return None
    if "*" in pan:
        return pan
    return "*" * max(0, len(pan) - 4) + pan[-4:]


def parse_authorization(data):
    """Datele mesajului (12): cod răspuns(2) PAN FS expirare(4) factură(6) aprobare(6) dată(6) oră(6) emitent FS. La respingere restul e gol.
    Întoarce dict; `approved` e adevărat doar la cod 00. PAN-ul se întoarce MASCAT, data de expirare nu se păstrează."""
    code = data[:2]
    parts = data[2:].split(chr(FS))
    pan = parts[0].strip() if parts else ""
    tail = parts[1] if len(parts) > 1 else ""
    return {
        "response_code": code,
        "approved": code.strip() in ("0", "00"),
        "card": mask_pan(pan),
        "invoice": tail[4:10].strip() or None,
        "approval_code": tail[10:16].strip() or None,
        "date": tail[16:22].strip() or None,
        "time": tail[22:28].strip() or None,
        "issuer": tail[28:].strip() or None,
    }


def decline_message(code):
    code = (code or "").strip()
    return RESPONSE_MESSAGES.get(code) or f"Plata a fost respinsă (cod {code or '?'})"
