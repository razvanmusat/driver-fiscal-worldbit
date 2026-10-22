"""POS bancar cu protocolul ECR Hypercom T7/FIP11 (Newland N910 Pro), legat prin USB (port COM virtual). Doar plata (MOL).

Fluxul unei plăți (secțiunea 5.2.1 din document):  10 (calculator) -> ACK -> 11 (POS: „am început”) -> ACK -> 12 (POS: rezultatul) -> ACK -> 13 -> ACK.
  - fără ACK la 10 sau fără 11 în `start_timeout_s` (30 s în document): terminalul e offline, nimic nu a pornit => eșec sigur, plata se poate relua;
  - 11 primit, dar 12 nu vine în `payment_timeout_s` (sau legătura cade după ce comanda a plecat): protocolul nu are interogare de stare, deci nu se știe dacă
    POS-ul a încasat (documentul: „terminalul poate să fi încasat și tipărit slip-ul”). Plata se consideră NEÎNCASATĂ: eșec cu „Reîncearcă plata” (`POS_NO_RESULT`),
    fără nicio decizie pentru casier; mesajul de eroare îi spune doar să nu reia plata dacă clientul a fost debitat;
  - 12 cu cod „00”: aprobat. Aprobarea se salvează în baza agentului ÎNAINTE de ACK-ul către POS; apoi 13 (best effort: plata rămâne aprobată
    chiar dacă 13 nu e confirmat; documentul spune că POS-ul revine singur la repaus după 30 s);
  - 12 cu alt cod: respins, nu iese bon.
Anularea (`VOI`) unei plăți aprobate: aceeași conversație, cu numărul chitanței POS în câmpul sumei; se folosește doar când bonul fiscal nu poate ieși.
Mesajele care nu sunt ale noastre (FIP-11 trimite tot traficul și către PIN pad) se ignoră; la mesajele noastre cu LRC greșit se răspunde NAK."""
import logging
import re
import threading
import time

from .. import serial_io
from ..drivers.base import Field
from ..errors import DriverError, NotConnected, PosDeclined
from . import ecr
from .base import POS_DRIVERS, PosDriver, PosResult

log = logging.getLogger("worldbit_fiscal")

MSG_ID = "MOL"


class _Link:
    """Nivelul de legătură: trimite cadre cu ACK/NAK și retrimitere, primește cadre ale noastre și le confirmă."""

    def __init__(self, port, ack_timeout, retries):
        self.port, self.ack_timeout, self.retries = port, ack_timeout, retries
        self.reader = ecr.Reader()
        self.events = []
        self.sent_once = False          # a plecat cel puțin un cadru întreg către POS

    def _pump(self, timeout):
        data = self.port.read(timeout)
        if data:
            self.events.extend(self.reader.feed(data))

    def ack(self):
        self.port.write(bytes([ecr.ACK]))

    def nak(self):
        self.port.write(bytes([ecr.NAK]))

    def send(self, body):
        """Trimite un mesaj; True dacă POS-ul l-a confirmat (ACK). Un NAK sau lipsa ACK-ului în `ack_timeout` = retrimitere, de cel mult `retries` ori."""
        data = ecr.frame(body)
        for _ in range(1 + self.retries):
            self.port.write(data)
            self.sent_once = True
            deadline = time.monotonic() + self.ack_timeout
            raspuns = None
            while raspuns is None:
                for i, ev in enumerate(self.events):
                    if ev[0] in ("ack", "nak"):
                        raspuns = self.events.pop(i)
                        break
                else:
                    ramas = deadline - time.monotonic()
                    if ramas <= 0:
                        break
                    self._pump(ramas)
            if raspuns and raspuns[0] == "ack":
                return True
        return False

    def wait_message(self, msg_type, timeout, ack=True, msg_id=MSG_ID):
        """Așteaptă mesajul `msg_type` al nostru (`msg_id`: MOL plată, STL decontare); îl confirmă cu ACK (dacă `ack`) și îl întoarce (id, tip, date).
        None la timeout."""
        deadline = time.monotonic() + timeout
        while True:
            while self.events:
                ev = self.events.pop(0)
                if ev[0] != "frame":
                    continue                            # ACK/NAK rătăcit
                parsed = ecr.parse_message(ev[1])
                if parsed is not None and parsed[0] == msg_id and parsed[1] == "11" and msg_type != "11" and ev[2]:
                    self.ack()                          # POS-ul retrimite „am început” (ACK-ul nostru nu i-a ajuns): altfel își abandonează plata
                    continue
                if parsed is None or parsed[0] != msg_id or parsed[1] != msg_type:
                    continue                            # nu e al nostru (ex. trafic către PIN pad): fără ACK, fără NAK
                if not ev[2]:
                    self.nak()                          # al nostru, dar deteriorat: POS-ul îl retrimite
                    continue
                if ack:
                    self.ack()
                return parsed
            ramas = deadline - time.monotonic()
            if ramas <= 0:
                return None
            self._pump(ramas)


class HypercomEcr(PosDriver):
    name = "hypercom_ecr"
    bank = "Raiffeisen"
    models = ("Newland N910 Pro",)           # terminalele pe care protocolul a fost verificat
    label = "ECR Hypercom (" + ", ".join(models) + ")"
    files = ("pos/hypercom_ecr.py", "pos/ecr.py")
    fields = (
        Field("port", "Port serial al POS-ului", "text", "",
              "Windows: COM4 (numărul din Manager dispozitive, la „Ports (COM & LPT)”). Linux: /dev/ttyACM0 sau /dev/ttyUSB0 (vezi cu `ls /dev/tty*` "
              "după ce conectezi POS-ul). POS-ul trebuie conectat și interfața activată pe terminal."),
        Field("baud", "Viteză (baud)", "int", 9600,
              "Viteza cu care calculatorul și POS-ul își trimit datele pe port (9600 biți pe secundă). Trebuie să fie aceeași pe ambele părți. Valoarea din documentul băncii."),
        Field("bytesize", "Biți de date", "int", 7,
              "Câți biți are fiecare caracter trimis. Documentul cere 7 (text simplu). Prin USB, terminalul poate cere 8: se verifică la primul test pe terminal."),
        Field("parity", "Paritate (N, E, O)", "text", "E",
              "O verificare simplă a fiecărui caracter, ca să se vadă dacă s-a stricat pe cablu. E = „pară”, cum cere documentul; N = fără verificare. "
              "Dacă POS-ul nu răspunde deloc la test, prima încercare e 8 biți de date și paritate N."),
        Field("stopbits", "Biți de stop", "int", 1,
              "Semnul care marchează sfârșitul fiecărui caracter. Documentul cere 1."),
        Field("ack_timeout_s", "Așteptare confirmare ACK (secunde)", "float", 1.0,
              "După fiecare mesaj, POS-ul trebuie să răspundă „am primit” (ACK). Atât așteaptă calculatorul acest răspuns, apoi retrimite mesajul. Documentul: cel mult 1 secundă."),
        Field("retries", "Retrimiteri la NAK / fără ACK", "int", 3,
              "De câte ori retrimite calculatorul același mesaj dacă POS-ul nu l-a primit bine (NAK) sau nu a răspuns. Documentul: 3."),
        Field("start_timeout_s", "Așteptare pornire plată (secunde)", "float", 30.0,
              "După ce calculatorul cere plata, POS-ul trebuie să spună „am pornit” (cere cardul). Dacă nu spune nimic în acest timp, plata nu a pornit și se poate relua. Documentul: 30 de secunde."),
        Field("payment_timeout_s", "Așteptare rezultat plată (secunde)", "float", 120.0,
              "Cât timp are clientul să pună cardul și să tasteze PIN-ul, până trimite POS-ul rezultatul. Dacă trece fără rezultat, plata apare ca eșuată („Reîncearcă plata”). "
              "Documentul cere minimum 60 de secunde; 120 lasă loc de manevră."),
        Field("confirm_delay_s", "Pauză înainte de confirmarea rezultatului (secunde)", "float", 1.5,
              "O pauză scurtă după ce calculatorul a primit rezultatul și înainte să-l confirme către POS, ca terminalul să apuce să-și termine chitanța. Documentul recomandă 1,5 secunde."),
    )
    port_factory = serial_io.SerialPort     # înlocuit în teste

    def __init__(self, settings):
        super().__init__(settings)
        self._busy = threading.Lock()       # o singură conversație cu terminalul odată (plată sau verificare de port)

    @classmethod
    def validate_settings(cls, settings):
        """Ridică ValueError cu mesajul pentru admin."""
        if not serial_io.valid_port_name(settings.get("port", "")):
            raise ValueError("Port POS: scrie portul serial al POS-ului (Windows: COM4, Linux: /dev/ttyACM0)")
        if settings.get("baud") not in serial_io.BAUDS:
            raise ValueError(f"Viteză POS: una din {', '.join(map(str, serial_io.BAUDS))}")
        if settings.get("bytesize") not in (7, 8):
            raise ValueError("Biți de date POS: 7 sau 8")
        if str(settings.get("parity", "")).upper() not in ("N", "E", "O"):
            raise ValueError("Paritate POS: N, E sau O")
        if settings.get("stopbits") not in (1, 2):
            raise ValueError("Biți de stop POS: 1 sau 2")
        for key, label in (("ack_timeout_s", "Așteptare ACK"), ("start_timeout_s", "Așteptare pornire plată"),
                           ("payment_timeout_s", "Așteptare rezultat plată")):
            if not 0.1 <= float(settings.get(key, 0)) <= 600:
                raise ValueError(f"{label}: între 0,1 și 600 de secunde")
        if not 0 <= float(settings.get("confirm_delay_s", -1)) <= 10:
            raise ValueError("Pauza înainte de confirmare: între 0 și 10 secunde")
        if not 0 <= int(settings.get("retries", -1)) <= 5:
            raise ValueError("Retrimiteri POS: între 0 și 5")

    def _open(self):
        s = self.settings
        port = self.port_factory(s["port"], int(s["baud"]), int(s["bytesize"]), str(s["parity"]).upper(), int(s["stopbits"]))
        port.open()
        return port

    def check_port(self):
        """Pentru /setari: deschide și închide portul, fără să trimită nimic. Întoarce textul pentru admin."""
        s = self.settings
        if not self._busy.acquire(blocking=False):
            return "O plată cu cardul e în curs pe acest POS; încearcă după ce se termină."
        try:
            try:
                port = self._open()
            except serial_io.SerialError as e:
                return f"POS: {e}"
            port.close()
        finally:
            self._busy.release()
        return f"Portul {s['port']} s-a deschis ({s['baud']} baud, {s['bytesize']}{str(s['parity']).upper()}{s['stopbits']}). Nu s-a trimis nimic către POS."

    def settlement(self):
        """Decontarea (STL): ca și cum ai apăsa „Decontare” pe terminal. Trimite comanda și confirmă „am început” (11); rezultatul e pe slip-ul
        tipărit de POS, nu ajunge la noi. True dacă terminalul a confirmat comanda. Nu ridică erori: dacă nu iese, se dă manual de pe terminal."""
        s = self.settings
        if not self._busy.acquire(blocking=False):
            log.warning("POS: decontare sărită, o plată e în curs")
            return False
        try:
            port = self._open()
            try:
                link = _Link(port, float(s["ack_timeout_s"]), int(s["retries"]))
                port.purge()
                if not link.send(ecr.message("STL", "10", "0" * 12)):
                    log.warning("POS: decontarea nu a fost confirmată de terminal (fără ACK)")
                    return False
                if link.wait_message("11", float(s["start_timeout_s"]), msg_id="STL") is None:
                    log.warning("POS: decontarea: terminalul nu a trimis 11")
                    return False
                log.info("POS: decontare pornită (STL), rezultatul e pe slip-ul terminalului")
                return True
            finally:
                port.close()
        except Exception as e:
            log.warning("POS: decontarea nu s-a putut trimite: %s", e)
            return False
        finally:
            self._busy.release()

    def sale(self, amount, job):
        try:
            suma = ecr.amount12(amount)
        except ValueError as e:
            raise DriverError(f"Suma de card nu poate fi trimisă la POS: {e}", code="POS_AMOUNT", retry_safe=True)
        return self._cu_portul(MSG_ID, suma, job)

    def void(self, invoice, job):
        """Anularea (VOI) unei plăți aprobate: câmpul sumei poartă numărul chitanței POS (documentul, 5.1.1), pe 12 cifre. Cu succes, job.pos devine „voided”
        (salvat înainte de ACK). Ridică DriverError dacă terminalul refuză sau nu confirmă; atunci chitanța se anulează de pe terminal."""
        invoice = str(invoice or "").strip()
        if not re.fullmatch(r"\d{1,12}", invoice):
            raise DriverError("Numărul chitanței POS lipsește sau nu e valid", code="POS_VOID_INVOICE", retry_safe=False)
        self._cu_portul("VOI", invoice.zfill(12), job)
        return True

    def _cu_portul(self, msg_id, camp, job):
        """Deschide portul, poartă conversația `msg_id` (MOL plată / VOI anulare), îl închide. Un singur acces la port odată."""
        s = self.settings
        ce = "plata" if msg_id == MSG_ID else "anularea"
        if not self._busy.acquire(blocking=False):
            raise DriverError("O altă operațiune pe acest POS e în curs", code="POS_BUSY", retry_safe=True)
        try:
            try:
                port = self._open()
            except serial_io.SerialError as e:
                raise NotConnected(str(e), code="POS_PORT")
            link = _Link(port, float(s["ack_timeout_s"]), int(s["retries"]))
            try:
                return self._conversatie(port, link, msg_id, camp, job)
            except serial_io.SerialError as e:
                if not link.sent_once:      # comanda n-a plecat: terminalul n-a aflat nimic
                    raise NotConnected(f"{e}. Operațiunea nu a pornit.", code="POS_PORT")
                log.warning("POS: legătura s-a întrerupt după trimiterea comenzii de %s: %s", ce, e)
                if msg_id == MSG_ID:
                    raise DriverError(f"Legătura cu POS-ul s-a întrerupt în timpul plății ({e}). Dacă clientul a fost debitat, nu relua plata.",
                                      code="POS_NO_RESULT", retry_safe=True)
                raise DriverError(f"Legătura cu POS-ul s-a întrerupt în timpul anulării ({e}). Verifică chitanța pe POS.",
                                  code="POS_NO_RESULT", retry_safe=False)
            finally:
                port.close()
        finally:
            self._busy.release()

    def _conversatie(self, port, link, msg_id, camp, job):
        s = self.settings
        plata = msg_id == MSG_ID
        ce = "plata" if plata else "anularea"
        port.purge()
        pornit = time.monotonic()
        log.info("POS: %s pornită (%s), port %s", ce, camp.lstrip("0") or "0", s["port"])
        if not link.send(ecr.message(msg_id, "10", camp)):
            raise NotConnected(f"POS-ul nu răspunde: nu a confirmat comanda de {ce}. Verifică cablul USB și că interfața e activată pe terminal.",
                               code="POS_OFFLINE")
        log.info("POS: comanda 10 confirmată (ACK)")
        if link.wait_message("11", float(s["start_timeout_s"]) - (time.monotonic() - pornit), msg_id=msg_id) is None:
            log.warning("POS: nu a venit 11 în %s s", s["start_timeout_s"])
            raise NotConnected(f"POS-ul nu a pornit {ce} în timp util. Verifică terminalul.", code="POS_OFFLINE")
        log.info("POS: 11 primit (terminalul a pornit), se așteaptă rezultatul până la %s s", s["payment_timeout_s"])
        rezultat = link.wait_message("12", float(s["payment_timeout_s"]), ack=False, msg_id=msg_id)   # ACK-ul vine după ce rezultatul e salvat
        if rezultat is None:
            log.warning("POS: nu a venit rezultatul (12) în %s s", s["payment_timeout_s"])
            if plata:
                raise DriverError("POS-ul nu a trimis rezultatul plății. Dacă clientul a fost debitat, nu relua plata.", code="POS_NO_RESULT", retry_safe=True)
            raise DriverError("POS-ul nu a confirmat anularea. Verifică chitanța pe POS.", code="POS_NO_RESULT", retry_safe=False)
        auth = ecr.parse_authorization(rezultat[2])
        log.info("POS: rezultat 12, cod răspuns %s, %s", auth["response_code"], "APROBAT" if auth["approved"] else "respins")
        raw = {k: auth[k] for k in ("response_code", "invoice", "approval_code", "issuer", "date", "time")}
        result = None
        if plata:
            if auth["approved"]:
                result = PosResult("approved", transaction_id=auth["invoice"], auth_code=auth["approval_code"], card_masked=auth["card"], raw=raw)
                job.set_pos(result.to_dict())       # salvată ÎNAINTE de ACK: după o cădere, aprobarea nu se pierde
            else:
                job.set_pos({"status": "declined", "transaction_id": None, "auth_code": None, "rrn": None, "card_masked": auth["card"], "raw": raw})
        elif auth["approved"]:
            salvat = dict(job.pos or {})
            salvat.update(status="voided", raw={**(salvat.get("raw") or {}), "void": raw})
            job.set_pos(salvat)                      # tot înainte de ACK: anularea nu se pierde
        self._confirma(link, msg_id)
        if not auth["approved"]:
            if plata:
                raise PosDeclined(ecr.decline_message(auth["response_code"]), code="POS_DECLINED", driver_code=auth["response_code"],
                                  driver_response=raw)
            raise DriverError(f"POS-ul a refuzat anularea: {ecr.decline_message(auth['response_code'])}", code="POS_VOID_DECLINED",
                              retry_safe=False, driver_code=auth["response_code"], driver_response=raw)
        return result

    def _confirma(self, link, msg_id=MSG_ID):
        """ACK la 12, pauza din document, apoi 13. Nimic de aici nu mai poate schimba rezultatul, deja salvat."""
        try:
            link.ack()
            time.sleep(float(self.settings["confirm_delay_s"]))
            if not link.send(ecr.message(msg_id, "13")):
                log.warning("POS: confirmarea rezultatului (13) nu a fost confirmată; rezultatul rămâne cum a spus terminalul")
        except Exception:
            log.exception("POS: confirmarea rezultatului (13) a eșuat; rezultatul rămâne cum a spus terminalul")


POS_DRIVERS[HypercomEcr.name] = HypercomEcr
