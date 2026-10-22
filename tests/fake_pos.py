"""POS ECR fals (protocolul Hypercom T7/FIP11 din documentul băncii) pe un port virtual (pty): agentul deschide `path` ca pe un port COM.
Poartă conversația din documentul de la secțiunea 5.2.1 și poate să se poarte urât: nu confirmă, nu pornește plata, nu dă rezultat, cade în mijloc,
trimite zgomot destinat PIN pad-ului, dă octeții unul câte unul sau un cadru cu LRC greșit."""
import os
import select
import termios
import threading
import time

from worldbit_fiscal.pos import ecr

PAN = "4111111111111111"
FS = chr(ecr.FS)


def approved_body(invoice="000123", approval="A1B2C3", msg_id="MOL"):
    return msg_id + "12." + "00" + PAN + FS + "2712" + invoice + approval + "261021" + "103045" + "VISA" + FS


def declined_body(code="51", empty_fields=True, msg_id="MOL"):
    return msg_id + "12." + code + ((FS + FS) if empty_fields else "")


class FakeEcrPos:
    def __init__(self, mode="approve", decline_code="51", delay_result=0.0, noise=False, dribble=False, bad_lrc_once=False,
                 nak_first=0, dup11=False, invoice="000123", approval="A1B2C3"):
        self.mode = mode                  # approve | decline | silent | no_start | no_result | no_ack13 | hangup_after_start
        self.decline_code, self.delay_result = decline_code, delay_result
        self.noise, self.dribble, self.bad_lrc_once, self.naks_left = noise, dribble, bad_lrc_once, nak_first
        self.dup11 = dup11               # retrimite „am început” (11) și așteaptă încă un ACK
        self.invoice, self.approval = invoice, approval
        self.void_mode = None             # comportamentul la VOI (implicit același ca `mode`)
        self.on_ack12 = None              # apelat când POS-ul primește ACK la rezultat (testul verifică ce era deja salvat)
        self.log = []                     # ("recv", mesaj) / ("ack",) / ("nak",) în ordinea primirii
        self.master, self.slave = os.openpty()
        attrs = termios.tcgetattr(self.slave)
        attrs[0], attrs[1], attrs[3] = 0, 0, 0       # fără prelucrări de terminal: octeți bruți
        termios.tcsetattr(self.slave, termios.TCSANOW, attrs)
        self.path = os.ttyname(self.slave)
        self.reader = ecr.Reader()
        self._queue = []
        self._stop = False
        self.thread = threading.Thread(target=self._loop, daemon=True, name="fake-pos")

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self._stop = True
        self.thread.join(timeout=3)
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass

    def received(self, msg_type=None):
        msgs = [e[1] for e in self.log if e[0] == "recv"]
        return [m for m in msgs if msg_type is None or m[3:5] == msg_type]

    def count(self, kind):
        return sum(1 for e in self.log if e[0] == kind)

    # ------------------------------------------------------------------ intern
    def _write(self, data):
        if self.dribble:
            for b in data:
                os.write(self.master, bytes([b]))
                time.sleep(0.002)
        else:
            os.write(self.master, data)

    def _send_frame(self, body, bad_lrc=False):
        data = bytearray(ecr.frame(body))
        if bad_lrc:
            data[-1] ^= 0x55
        self._write(bytes(data))

    def _next_event(self, timeout):
        if not self._queue:
            try:
                ready, _, _ = select.select([self.master], [], [], timeout)
                if ready:
                    self._queue.extend(self.reader.feed(os.read(self.master, 4096)))
            except OSError:
                return None
        if not self._queue:
            return None
        ev = self._queue.pop(0)
        if ev[0] == "frame" and ev[2]:
            self.log.append(("recv", ev[1]))
        elif ev[0] in ("ack", "nak"):
            self.log.append((ev[0],))
        return ev

    def _wait(self, kinds, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ev = self._next_event(0.05)
            if ev and (ev[0] in kinds or (ev[0] == "frame" and "frame" in kinds)):
                return ev
        return None

    def _loop(self):
        while not self._stop:
            ev = self._next_event(0.05)
            if ev and ev[0] == "frame" and ev[2]:
                parsed = ecr.parse_message(ev[1])
                if parsed and parsed[0] == "STL" and parsed[1] == "10":
                    try:
                        self._settlement()
                    except OSError:
                        return
                elif parsed and parsed[0] in ("MOL", "VOI") and parsed[1] == "10":
                    try:
                        self._transaction(parsed[0])
                    except OSError:
                        return

    def _settlement(self):
        """Decontarea (secțiunea 5.2.2): 10 -> ACK -> 11 -> ACK de la calculator. Fără mesajul 12."""
        if self.mode == "silent":
            return
        self._write(bytes([ecr.ACK]))
        if self.mode == "no_start":
            return
        self._send_frame("STL11.")
        if self._wait(("ack", "nak"), 3):
            self.settled = getattr(self, "settled", 0) + 1

    def _transaction(self, msg_id="MOL"):
        mode = (self.void_mode or self.mode) if msg_id == "VOI" else self.mode
        if mode == "silent":
            return
        if self.naks_left > 0:
            self.naks_left -= 1
            self._write(bytes([ecr.NAK]))
            return
        self._write(bytes([ecr.ACK]))
        if self.noise:
            self._send_frame("PIN01.0000")             # mesaj pentru PIN pad: ECR nu trebuie să-l confirme
            self._write(b"\x04\x00xx")                  # zgomot
        if mode == "no_start":
            return
        self._send_frame(msg_id + "11.")
        if self._wait(("ack", "nak"), 3) is None:
            return
        if self.dup11:
            self._send_frame(msg_id + "11.")
            if self._wait(("ack", "nak"), 3) is None:
                return
        if mode == "hangup_after_start":
            self._stop = True
            os.close(self.master)                       # POS-ul „dispare”: citirea pe portul agentului pică
            return
        if mode == "no_result":
            return
        time.sleep(self.delay_result)
        body = (approved_body(self.invoice, self.approval, msg_id) if mode in ("approve", "no_ack13")
                else declined_body(self.decline_code, msg_id=msg_id))
        self._send_frame(body, bad_lrc=self.bad_lrc_once)
        reply = self._wait(("ack", "nak"), 3)
        if reply and reply[0] == "nak" and self.bad_lrc_once:
            self.bad_lrc_once = False
            self._send_frame(body)
            reply = self._wait(("ack", "nak"), 3)
        if reply and reply[0] == "ack" and self.on_ack12:
            self.on_ack12()
        ev = self._wait(("frame",), 5)
        if ev and ev[0] == "frame" and ev[2] and ecr.parse_message(ev[1]) and ecr.parse_message(ev[1])[1] == "13" and mode != "no_ack13":
            self._write(bytes([ecr.ACK]))
