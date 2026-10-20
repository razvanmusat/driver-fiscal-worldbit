"""Sertarul de bani, pornit de un modul USB-serial (ex. Prolific PL2303 pe COM3): comanda se scrie pe portul COM ca într-un fișier, exact ca
`echo 1 > \\\\.\\COM3` (Windows folosește setările portului din sistem). Doar agentul poate face asta: portul e al calculatorului de casă,
iar browserul nu poate scrie pe el.

Setări (config `drawer`): `port` (gol = fără sertar), `command` (textul trimis; după el se adaugă CRLF, ca la `echo`), `auto_open` (la fiecare bon
tipărit cu numerar, inclusiv plată mixtă). Deschiderea e „best effort”: un sertar care nu răspunde nu strică niciodată un bon deja tipărit.
"""
import logging
import re
import threading

log = logging.getLogger("worldbit_fiscal")

DEFAULTS = {"port": "", "command": "1", "auto_open": True}
TIMEOUT_S = 3.0
_COM = re.compile(r"^COM\d{1,3}$", re.IGNORECASE)
_LOCK = threading.Lock()   # un singur acces la port odată (buton + deschidere automată)


def settings(cfg):
    return {**DEFAULTS, **(cfg.get("drawer") or {})}


def configured(cfg):
    return bool(str(settings(cfg)["port"]).strip())


def valideaza(port, command):
    """Ridică ValueError cu mesajul pentru admin. Portul: gol, COMn sau o cale (pentru teste); comanda: text tipăribil, scurt."""
    port = (port or "").strip()
    command = command if command is not None else ""
    if re.search(r"[\x00-\x1f\x7f]", port):
        raise ValueError("Port sertar: caractere invalide")
    if not command.strip() or len(command) > 20 or re.search(r"[^\x20-\x7e]", command):
        raise ValueError("Comandă sertar: 1-20 de caractere tipăribile (implicit 1)")
    return port, command.strip()


def device_path(port):
    """COM3 -> \\\\.\\COM3 (prefixul e obligatoriu pentru COM10+ și merge și pentru COM1-9); orice altceva rămâne cale de fișier."""
    port = port.strip()
    return "\\\\.\\" + port.upper() if _COM.match(port) else port


def _write(path, data):
    with open(path, "wb", buffering=0) as f:   # „wb”, ca la `echo 1 > \\.\COM3`
        f.write(data)


def _mesaj(e, port):
    if isinstance(e, FileNotFoundError):
        return f"Portul {port} nu există (modulul USB al sertarului e conectat?)"
    if isinstance(e, PermissionError):
        return f"Portul {port} e folosit de alt program sau nu are drepturi de acces"
    return f"Nu s-a putut scrie pe portul {port}: {e}"


def open_drawer(cfg):
    """Trimite comanda de deschidere. Întoarce (True, None, None) sau (False, cod, mesaj)."""
    s = settings(cfg)
    port = str(s["port"]).strip()
    if not port:
        return False, "DRAWER_NOT_CONFIGURED", "Sertarul nu e configurat (portul lipsește în /setari)"
    data = (str(s["command"]) + "\r\n").encode("ascii", "replace")
    iesire = {}

    def lucreaza():
        try:
            _write(device_path(port), data)
            iesire["ok"] = True
        except OSError as e:
            iesire["eroare"] = e

    with _LOCK:
        fir = threading.Thread(target=lucreaza, daemon=True, name="drawer")
        fir.start()
        fir.join(TIMEOUT_S)
        if fir.is_alive():   # port blocat: nu agățăm agentul
            log.warning("sertar: portul %s nu a răspuns în %s s", port, TIMEOUT_S)
            return False, "DRAWER_TIMEOUT", f"Portul {port} nu a răspuns în {int(TIMEOUT_S)} secunde"
    if iesire.get("ok"):
        log.info("sertar: deschis (%s)", port)
        return True, None, None
    mesaj = _mesaj(iesire["eroare"], port)
    log.warning("sertar: %s", mesaj)
    return False, "DRAWER_FAILED", mesaj
