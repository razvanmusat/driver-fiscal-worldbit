"""Configurația locală a agentului (un fișier JSON). Driverul și modelul POS se aleg la instalare (comanda `init`)."""
import copy
import json
import os
import sys
import tempfile

DEFAULT_PORT = 4747

DEFAULTS = {
    "port": DEFAULT_PORT,
    "agent_id": None,
    "allowed_origins": [],
    "driver": "fisco",
    "drivers": {},
    "pos": {"model": "none", "settings": {}},
    # sertarul de bani (modul USB-serial pe un port COM); port gol = fără sertar. Vezi drawer.py
    "drawer": {"port": "", "command": "1", "auto_open": True},
    # reîncercarea AUTOMATĂ a eșecurilor trecătoare documentate de drivere (fără operator): de câte ori și la câte secunde
    "auto_retry": {"max": 3, "delay_s": 3.0},
    # Termenul total (de la pornirea jobului) după care un bon neconfirmat ca emis NU mai e considerat în așteptare: jobul se închide ca eșuat,
    # bonul deschis din Dressbit se eliberează, iar casierul reia plata normal. Raportul X/Z are termen mai lung (tipărirea lui durează mai mult).
    "max_wait_s": 30,
    "max_wait_report_s": 120,
    "admin": None,
    # joburile terminate mai vechi de atâtea zile se șterg din baza agentului (Dressbit își păstrează istoricul; agentul ține doar ce îi trebuie ca să
    # recunoască o plată reluată). Joburile în lucru sau incerte nu se șterg niciodată.
    "retention_days": 7,
    # de unde se descarcă actualizarea (implicit ultima versiune din repo-ul public de pe GitHub); se schimbă doar în config.json
    "update_url": None,
}


def home_dir():
    env = os.environ.get("WORLDBIT_FISCAL_HOME")
    if env:
        return env
    if sys.platform == "win32":
        return os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "WorldbitFiscalDriver")
    return os.path.join(os.path.expanduser("~"), ".worldbit-fiscal-driver")


def config_path(home=None):
    return os.path.join(home or home_dir(), "config.json")


def load(home=None):
    path = config_path(home)
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            stored = json.load(f)
        for key in DEFAULTS:
            if key in stored:
                cfg[key] = stored[key]
    return cfg


def save(cfg, home=None):
    home = home or home_dir()
    os.makedirs(home, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=home, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, config_path(home))
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
