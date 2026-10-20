"""Pagina /setari (HTML simplu, server-side): doar câmpurile driverului ales la instalare."""
import copy
import html
import re
from decimal import Decimal, InvalidOperation

from . import drawer as drawer_mod
from .contract import percent_key
from .pos.base import POS_DRIVERS

_ORIGIN = re.compile(r"^https?://[^/\s]+$")

_CSS = """
body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem;color:#222}
h1{font-size:1.4rem}fieldset{margin:1rem 0;border:1px solid #ccc;border-radius:6px}
label{display:block;margin:.8rem 0 .2rem;font-weight:600}small{color:#666;display:block}
input[type=text],input[type=password],input[type=number],textarea,select{width:100%;padding:.4rem;box-sizing:border-box}
textarea{min-height:5rem;font-family:monospace}button{padding:.5rem 1rem;margin:.5rem .5rem .5rem 0}
.ok{background:#e7f6e7;padding:.6rem;border-radius:6px}.err{background:#fde8e8;padding:.6rem;border-radius:6px}
pre{background:#f4f4f4;padding:.6rem;overflow:auto}
"""


def _e(value):
    return html.escape(str(value), quote=True)


def _page(title, body):
    return (f"<!doctype html><html lang='ro'><head><meta charset='utf-8'><title>{_e(title)}</title>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{_CSS}</style></head>"
            f"<body><h1>{_e(title)}</h1>{body}</body></html>")


def login_page(error=None, configured=True):
    if not configured:
        return _page("Setări driver fiscal", "<p class='err'>Parola de admin nu e setată. "
                     "Rulează <code>python -m worldbit_fiscal init</code>.</p>")
    msg = f"<p class='err'>{_e(error)}</p>" if error else ""
    return _page("Setări driver fiscal", f"{msg}<form method='post' action='/setari/login'>"
                 "<label>Parola de admin</label><input type='password' name='password' autofocus required>"
                 "<button type='submit'>Autentificare</button></form>")


def _map_text(value):
    return "\n".join(f"{k}={v}" for k, v in value.items())


def _field_html(field, value):
    name = "d_" + field.key
    help_ = f"<small>{_e(field.help)}</small>" if field.help else ""
    if field.kind == "map":
        control = f"<textarea name='{name}'>{_e(_map_text(value))}</textarea>"
    else:
        control = f"<input type='text' name='{name}' value='{_e(value)}'>"
    return f"<label>{_e(field.label)}</label>{control}{help_}"


def settings_page(cfg, driver_cls, driver_values, message=None, error=None, output=None):
    parts = []
    if message:
        parts.append(f"<p class='ok'>{_e(message)}</p>")
    if error:
        parts.append(f"<p class='err'>{_e(error)}</p>")
    if output:
        parts.append(f"<pre>{_e(output)}</pre>")
    pos_options = "".join(
        f"<option value='{_e(m)}'{' selected' if m == cfg['pos']['model'] else ''}>{_e(m)}</option>"
        for m in ["none", *POS_DRIVERS])
    driver_fields = "".join(_field_html(f, driver_values[f.key]) for f in driver_cls.fields)
    discover = ("<button type='submit' formaction='/setari/devices'>Detectează dispozitive</button>"
                if hasattr(driver_cls, "discover") else "")
    parts.append(f"""
<form method='post' action='/setari/save'>
<fieldset><legend>General</legend>
<label>Port agent</label><input type='text' name='port' value='{_e(cfg['port'])}'>
<small>Se aplică după repornirea serviciului.</small>
<label>Origini permise (aplicațiile web)</label>
<textarea name='allowed_origins'>{_e(chr(10).join(cfg['allowed_origins']))}</textarea>
<small>Câte una pe linie, ex. https://dressbit.exemplu.ro (fără / la final).</small>
</fieldset>
<fieldset><legend>Driver fiscal: {_e(driver_cls.name)}</legend>
<small>Driverul se schimbă doar la reinstalare (python -m worldbit_fiscal init).</small>
{driver_fields}
</fieldset>
<fieldset><legend>Sertar de bani</legend>
<input type='hidden' name='drawer_present' value='1'>
<label>Port sertar (ex. COM3)</label><input type='text' name='drawer_port' value='{_e(drawer_mod.settings(cfg)['port'])}'>
<small>Portul COM al modulului USB al sertarului (Manager dispozitive). Gol = fără sertar.</small>
<label>Comandă sertar</label><input type='text' name='drawer_command' value='{_e(drawer_mod.settings(cfg)['command'])}'>
<small>Textul scris pe port (ca în `echo 1 > \\\\.\\COM3`); după el se adaugă automat sfârșit de linie.</small>
<label><input type='checkbox' name='drawer_auto'{' checked' if drawer_mod.settings(cfg)['auto_open'] else ''}> Deschide automat la încasările cu numerar (inclusiv plata mixtă)</label>
<button type='submit' formaction='/setari/drawer-test'>Deschide sertarul (test)</button>
</fieldset>
<fieldset><legend>POS bancar (în așteptare)</legend>
<label>Model POS</label><select name='pos_model'>{pos_options}</select>
<small>„none” = agentul nu apelează POS-ul; plata cu cardul se face în afara agentului.</small>
</fieldset>
<button type='submit'>Salvează</button>
<button type='submit' formaction='/setari/test'>Testează conexiunea</button>
{discover}
</form>
<form method='post' action='/setari/password'><fieldset><legend>Schimbă parola</legend>
<label>Parola nouă (minim 8 caractere)</label><input type='password' name='new_password' minlength='8'>
<button type='submit'>Schimbă parola</button></fieldset></form>
<form method='post' action='/setari/logout'><button type='submit'>Ieșire</button></form>""")
    return _page("Setări driver fiscal", "".join(parts))


def parse_settings_form(form, cfg, driver_cls):
    """form: dict nume -> valoare (text). Întoarce o configurație nouă sau ridică ValueError cu mesajul pentru admin."""
    new = copy.deepcopy(cfg)

    try:
        port = int(form.get("port", "").strip())
    except ValueError:
        raise ValueError("Port invalid")
    if not 1024 <= port <= 65535:
        raise ValueError("Portul trebuie să fie între 1024 și 65535")
    new["port"] = port

    origins = []
    for line in form.get("allowed_origins", "").splitlines():
        line = line.strip()
        if not line:
            continue
        if not _ORIGIN.match(line):
            raise ValueError(f"Origine invalidă: {line} (formă: https://gazda[:port], fără cale)")
        origins.append(line)
    new["allowed_origins"] = origins

    if form.get("drawer_present"):   # câmpurile sertarului au fost trimise (un formular fără ele nu atinge setarea)
        port, command = drawer_mod.valideaza(form.get("drawer_port", ""), form.get("drawer_command", ""))
        new["drawer"] = {"port": port, "command": command, "auto_open": form.get("drawer_auto") is not None}

    model = form.get("pos_model", "none")
    if model != "none" and model not in POS_DRIVERS:
        raise ValueError("Model POS necunoscut")
    new["pos"]["model"] = model

    values = {}
    for f in driver_cls.fields:
        raw = form.get("d_" + f.key, "")
        try:
            if f.kind == "int":
                values[f.key] = int(raw.strip())
            elif f.kind == "float":
                values[f.key] = float(raw.strip())
            elif f.kind == "map":
                values[f.key] = _parse_map(raw)
            else:
                values[f.key] = raw.strip()
        except ValueError as e:
            raise ValueError(f"{f.label}: {e}" if str(e) else f"{f.label}: valoare invalidă")
    new.setdefault("drivers", {})[driver_cls.name] = values
    return new


def _parse_map(raw):
    result = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"linie invalidă „{line}” (formă: 21=1)")
        key, value = (p.strip() for p in line.split("=", 1))
        try:
            key = percent_key(Decimal(key))
        except InvalidOperation:
            raise ValueError(f"procent invalid „{key}”")
        if not value:
            raise ValueError(f"valoare lipsă pentru {key}")
        result[key] = value
    return result
