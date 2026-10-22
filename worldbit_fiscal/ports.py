"""Detectarea porturilor seriale (USB) ale echipamentelor, pentru lista „Detectează porturi" din /setari. Doar biblioteca standard.

Ce se salvează în setări e calea cea mai STABILĂ a echipamentului:
  - Linux: `/dev/serial/by-id/usb-<producător>_<model>_<serie>-if00`. Numele vine din numărul de serie al echipamentului, nu din portul USB, deci rămâne același
    oricum ai muta echipamentul. `/dev/ttyACM0` / `/dev/ttyUSB0` se numerotează în ordinea conectării și se pot schimba; apar doar dacă echipamentul nu are
    identificator fix (atunci se afișează un avertisment și o regulă udev sugerată).
  - Windows: `COMn`. Windows ține numărul COM legat de echipament doar dacă acesta are număr de serie USB; fără el, numărul se leagă de portul USB.
Fiecare element: {"path", "label", "detail", "stable", "note", "udev_rule"}."""
import glob
import json
import os
import re
import subprocess
import sys

_COM = re.compile(r"\((COM\d+)\)")


def list_ports(dev_root="/dev", sys_root="/sys"):
    """Lista porturilor; niciodată o excepție (pagina de setări nu trebuie să pice din cauza unei liste de porturi)."""
    try:
        if sys.platform == "win32":
            return _windows_ports()
        if sys.platform == "darwin":
            return [{"path": p, "label": os.path.basename(p), "detail": "", "stable": False, "note": "Numele se poate schimba dacă muți portul USB.",
                     "udev_rule": ""} for p in sorted(glob.glob("/dev/cu.usb*"))]
        return linux_ports(dev_root, sys_root)
    except Exception:
        return []


# ----------------------------------------------------------------------------------------------------------- Linux
def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _usb_info(tty, sys_root):
    """Atributele USB ale lui `ttyACM0` / `ttyUSB0`: urcă din /sys/class/tty/<tty>/device până la dosarul dispozitivului USB (cel cu idVendor)."""
    p = os.path.realpath(os.path.join(sys_root, "class", "tty", tty, "device"))
    for _ in range(8):
        if os.path.exists(os.path.join(p, "idVendor")):
            return {"vid": _read(os.path.join(p, "idVendor")), "pid": _read(os.path.join(p, "idProduct")),
                    "manufacturer": _read(os.path.join(p, "manufacturer")), "product": _read(os.path.join(p, "product")),
                    "serial": _read(os.path.join(p, "serial"))}
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return {}


def _udev_rule(info, simbol):
    if not info.get("vid") or not info.get("pid"):
        return ""
    serie = f', ATTRS{{serial}}=="{info["serial"]}"' if info.get("serial") else ""
    return f'SUBSYSTEM=="tty", ATTRS{{idVendor}}=="{info["vid"]}", ATTRS{{idProduct}}=="{info["pid"]}"{serie}, SYMLINK+="{simbol}"'


def linux_ports(dev_root="/dev", sys_root="/sys"):
    """Echipamentele USB-serial: întâi cele cu cale stabilă (by-id), apoi cele care nu au una."""
    gasite, acoperite = [], set()
    for link in sorted(glob.glob(os.path.join(dev_root, "serial", "by-id", "*"))):
        tty = os.path.basename(os.path.realpath(link))
        acoperite.add(tty)
        info = _usb_info(tty, sys_root)
        nume = info.get("product") or os.path.basename(link)
        producator = f" ({info['manufacturer']})" if info.get("manufacturer") else ""
        detalii = [f"USB {info['vid']}:{info['pid']}"] if info.get("vid") else []
        detalii.append(f"serie {info['serial']}" if info.get("serial") else "fără număr de serie")
        detalii.append(f"acum {tty}")
        # calea by-id rămâne fixă oricum muți echipamentul; fără număr de serie, două echipamente identice ar avea aceeași cale (avertisment)
        gasite.append({"path": link, "label": f"{nume}{producator}", "detail": " · ".join(detalii), "stable": True,
                       "note": "" if info.get("serial") else "Echipamentul nu are număr de serie: calea e fixă, dar dacă ai două echipamente identice nu se pot deosebi.",
                       "udev_rule": ""})
    for pattern in ("ttyACM*", "ttyUSB*"):
        for nod in sorted(glob.glob(os.path.join(dev_root, pattern))):
            tty = os.path.basename(nod)
            if tty in acoperite:
                continue
            info = _usb_info(tty, sys_root)
            nume = info.get("product") or tty
            producator = f" ({info['manufacturer']})" if info.get("manufacturer") else ""
            gasite.append({"path": nod, "label": f"{nume}{producator}", "detail": (f"USB {info['vid']}:{info['pid']} · " if info.get("vid") else "") + "fără cale stabilă",
                           "stable": False,
                           "note": "Nu are identificator fix: numărul din /dev/ttyACM0 sau /dev/ttyUSB0 se poate schimba dacă muți echipamentul în alt port USB. "
                                   "Fixează-l cu o regulă udev (se pune ca root în /etc/udev/rules.d/99-echipamente.rules) și scrie aici /dev/pos.",
                           "udev_rule": _udev_rule(info, "pos")})
    return gasite


# --------------------------------------------------------------------------------------------------------- Windows
def windows_ports(registry_ports, cim_json):
    """Pură, pentru teste. `registry_ports` = {nume dispozitiv: "COMn"} din HKLM\\HARDWARE\\DEVICEMAP\\SERIALCOMM; `cim_json` = ieșirea PowerShell
    (Name, PNPDeviceID) a dispozitivelor cu „(COMn)" în nume."""
    try:
        intrari = json.loads(cim_json) if cim_json.strip() else []
    except ValueError:
        intrari = []
    if isinstance(intrari, dict):
        intrari = [intrari]
    nume = {}
    for e in intrari:
        m = _COM.search(e.get("Name") or "")
        if m:
            nume[m.group(1)] = (e.get("Name") or "", e.get("PNPDeviceID") or "")
    gasite = []
    for com in sorted(set(registry_ports.values()) | set(nume), key=lambda c: int(c[3:])):
        friendly, pnp = nume.get(com, ("", ""))
        usb = pnp.upper().startswith("USB\\")
        instanta = pnp.split("\\")[-1] if pnp else ""
        cu_serie = usb and "&" not in instanta
        if usb and not cu_serie:
            nota = ("Echipamentul nu are număr de serie USB: Windows leagă numărul COM de portul USB în care e băgat. "
                    "Dacă îl muți în alt port, COM-ul se poate schimba; lasă-l mereu în același port.")
        else:
            nota = ""
        gasite.append({"path": com, "label": (_COM.sub("", friendly).strip() or com) if friendly else com,
                       "detail": f"{com}" + (" · USB" if usb else ""), "stable": not usb or cu_serie, "note": nota, "udev_rule": ""})
    return gasite


def _windows_ports():
    registry = {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as k:
            i = 0
            while True:
                try:
                    nume, valoare, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                registry[nume] = valoare
                i += 1
    except OSError:
        pass
    cim = ""
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                            "Get-CimInstance Win32_PnPEntity -Filter \"Name like '%(COM%'\" | Select-Object Name,PNPDeviceID | ConvertTo-Json -Compress"],
                           capture_output=True, text=True, timeout=12, creationflags=0x08000000)
        cim = r.stdout or ""
    except (OSError, subprocess.SubprocessError):
        pass
    return windows_ports(registry, cim)
