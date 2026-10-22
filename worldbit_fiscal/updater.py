"""Butonul „Actualizează” din /setari: descarcă ultima versiune de pe GitHub (repo public), verifică dacă are modificări pentru ce rulează AICI (miezul
agentului, driverul fiscal ales și modelul de POS ales) și doar atunci o instalează. Altfel: „nimic de actualizat”.

Verificarea compară, fișier cu fișier (SHA-256), pachetul instalat cu cel descărcat, doar pe fișierele care contează aici: tot ce nu e în `drivers/` și
`pos/` (miezul), plus fișierele driverului și ale modelului POS alese (`files` din clasele lor). Nu contează: versiunea (`version.py`), listele de
înregistrare (`drivers/__init__.py`, `pos/__init__.py`) și driverele/modelele neinstalate aici. O versiune nouă fără modificări relevante nu se instalează.

Instalarea merge doar pe instalarea normală de pe Windows (task programat `WorldbitFiscalDriver`): agentul pregătește pachetul nou într-un dosar de lucru,
apoi pornește un al doilea task, independent de agent, care oprește agentul, schimbă dosarul, îl repornește și așteaptă să răspundă cu versiunea nouă;
dacă nu răspunde în 40 s, pune înapoi versiunea veche. Se face doar la apăsarea butonului, niciodată automat."""
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .version import __version__

log = logging.getLogger("worldbit_fiscal")

DEFAULT_URL = "https://github.com/razvanmusat/driver-fiscal-worldbit/archive/refs/heads/main.zip"
MAX_ZIP_BYTES = 30 * 1024 * 1024
MAX_UNZIPPED_BYTES = 150 * 1024 * 1024
TASK = "WorldbitFiscalDriver"
UPDATE_TASK = "WorldbitFiscalUpdate"
_VERSION = re.compile(r'__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"')
_REGISTRY = ("drivers/__init__.py", "pos/__init__.py", "version.py")


class UpdateError(Exception):
    def __init__(self, message, detail=None):
        super().__init__(message)
        self.detail = detail


_PREFLIGHT = r"""
import sys, pathlib
root = pathlib.Path(sys.argv[1]); sys.path.insert(0, str(root))
for f in sorted((root / "worldbit_fiscal").rglob("*.py")):
    compile(f.read_text(encoding="utf-8"), str(f.relative_to(root)), "exec")
import worldbit_fiscal.version as v
import worldbit_fiscal.__main__, worldbit_fiscal.server, worldbit_fiscal.runner, worldbit_fiscal.setari, worldbit_fiscal.updater
import worldbit_fiscal.drivers, worldbit_fiscal.pos
print(v.__version__)
"""


def python_exe():
    """Interpretorul cu care rulează agentul. Ca serviciu rulează cu pythonw.exe (fără consolă): pentru verificări și pentru captarea ieșirii se folosește python.exe de lângă el."""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        candidat = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(candidat):
            return candidat
    return exe


def _tail(text, n=12):
    linii = [l.rstrip() for l in (text or "").strip().splitlines() if l.strip()]
    return "\n".join(linii[-n:])


def preflight(new_pkg, target):
    """Verifică pachetul nou ÎNAINTE de orice oprire a agentului: compilează toate fișierele (erori de sintaxă) și le importă cu același Python ca agentul
    (module lipsă, erori la import). Ridică UpdateError cu motivul exact; agentul care rulează nu a fost atins."""
    try:
        r = subprocess.run([python_exe(), "-E", "-s", "-c", _PREFLIGHT, str(Path(new_pkg).parent)], capture_output=True, text=True,
                           timeout=60, cwd=tempfile.gettempdir())
    except (OSError, subprocess.SubprocessError) as e:
        raise UpdateError(f"verificarea pachetului nou nu a putut rula ({e})")
    if r.returncode != 0:
        detaliu = _tail(r.stderr)
        raise UpdateError(f"nu poate porni: {detaliu.splitlines()[-1] if detaliu else 'eroare necunoscută la verificare'}", detail=detaliu)
    declarata = (r.stdout.strip().splitlines() or [""])[-1]
    if declarata != target:
        raise UpdateError(f"pachetul declară versiunea {declarata}, nu {target}")


def installed_package():
    return Path(__file__).resolve().parent


def read_version(pkg):
    try:
        found = _VERSION.search((Path(pkg) / "version.py").read_text(encoding="utf-8"))
    except OSError:
        found = None
    if not found:
        raise UpdateError("pachetul descărcat nu are o versiune validă (worldbit_fiscal/version.py)")
    return tuple(int(x) for x in found.groups())


def fmt(version):
    return ".".join(str(x) for x in version)


def download(url, dest, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "worldbit-fiscal-driver/" + __version__})
    total = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ZIP_BYTES:
                raise UpdateError("arhiva descărcată e prea mare")
            out.write(chunk)


def extract(zip_path, dest):
    """Dezarhivează în `dest` (doar fișiere obișnuite, fără ieșiri din dosar) și întoarce dosarul `worldbit_fiscal` din arhivă."""
    dest = Path(dest).resolve()
    try:
        with zipfile.ZipFile(zip_path) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED_BYTES:
                raise UpdateError("arhiva descărcată e prea mare după dezarhivare")
            for info in z.infolist():
                target = (dest / info.filename).resolve()
                if dest != target and dest not in target.parents:
                    raise UpdateError("arhiva conține o cale nepermisă")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if (info.external_attr >> 28) == 0xA:       # legătură simbolică
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(info))
    except zipfile.BadZipFile:
        raise UpdateError("fișierul descărcat nu e o arhivă zip validă")
    candidates = sorted(dest.rglob("worldbit_fiscal/version.py"), key=lambda p: len(p.parts))
    if not candidates:
        if any(dest.rglob("worldbit_fiscal/__main__.py")):
            raise UpdateError("pe GitHub nu există încă o versiune nouă a agentului (codul de acolo e mai vechi și nu are număr de versiune)")
        raise UpdateError("arhiva nu conține pachetul worldbit_fiscal")
    return candidates[0].parent


def module_files(cfg):
    """Fișierele driverului fiscal și ale modelului POS alese pe acest calculator."""
    from .drivers import DRIVERS
    from .pos.base import POS_DRIVERS
    files = set(DRIVERS[cfg["driver"]].files)
    model = (cfg.get("pos") or {}).get("model", "none")
    if model != "none" and model in POS_DRIVERS:
        files |= set(POS_DRIVERS[model].files)
    return files


def relevant_hashes(pkg, modules):
    """{cale relativă: sha256} pentru fișierele care contează aici (vezi docstring-ul modulului)."""
    pkg = Path(pkg)
    out = {}
    for f in sorted(pkg.rglob("*.py")):
        rel = f.relative_to(pkg).as_posix()
        if "__pycache__" in rel.split("/") or rel in _REGISTRY:
            continue
        parts = rel.split("/")
        if len(parts) > 1 and parts[0] in ("drivers", "pos") and parts[-1] != "base.py" and rel not in modules:
            continue                              # driver sau model care nu rulează aici
        out[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def decide(local_pkg, new_pkg, modules):
    """Întoarce (stare, mesaj): `current` (aceeași versiune sau mai veche), `irrelevant` (mai nouă, dar fără modificări pentru acest calculator) sau `update`."""
    local, new = read_version(local_pkg), read_version(new_pkg)
    if new <= local:
        return "current", f"Ai deja ultima versiune ({fmt(local)})."
    if relevant_hashes(local_pkg, modules) == relevant_hashes(new_pkg, modules):
        return "irrelevant", (f"Există versiunea {fmt(new)}, dar nu are modificări pentru driverul fiscal și POS-ul de pe acest calculator. "
                              f"Nimic de actualizat (rămâi pe {fmt(local)}).")
    return "update", f"Actualizare disponibilă: {fmt(local)} -> {fmt(new)}."


def can_install():
    """(da/nu, motiv): doar pe instalarea normală de pe Windows, cu task programat."""
    if sys.platform != "win32":
        return False, "actualizarea din /setari merge doar pe Windows; aici reinstalează agentul"
    if "worldbitfiscaldriver" not in str(installed_package()).lower():
        return False, "agentul nu rulează din instalarea normală (folderul WorldbitFiscalDriver)"
    r = subprocess.run(["schtasks", "/query", "/tn", TASK], capture_output=True)
    if r.returncode != 0:
        return False, f"nu există task-ul programat {TASK}"
    return True, ""


_PS = r'''
$ErrorActionPreference = 'Continue'
$plan = Get-Content (Join-Path $PSScriptRoot 'plan.json') -Raw | ConvertFrom-Json
$log = Join-Path $PSScriptRoot 'last.txt'
$resFile = Join-Path $PSScriptRoot 'result.json'
function Say($m) { ("{0} {1}" -f (Get-Date -Format 's'), $m) | Out-File $log -Append -Encoding utf8 }
function Finish($ok, $reason) {
    @{ ok = $ok; from = $plan.old_version; to = $plan.version; reason = $reason; at = (Get-Date -Format 's') } | ConvertTo-Json | Out-File $resFile -Encoding utf8
}
function Stop-Agent {
    Stop-ScheduledTask -TaskName $plan.task -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" | Where-Object { $_.CommandLine -like '*worldbit_fiscal*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}
function Wait-Health {
    # limită de timp reală (nu număr de încercări: o încercare eșuată poate dura câteva secunde)
    $fim = (Get-Date).AddSeconds(40)
    while ((Get-Date) -lt $fim) {
        try { $h = Invoke-RestMethod "http://127.0.0.1:$($plan.port)/health" -TimeoutSec 2; if ($h.version -eq $plan.version) { return $true } } catch { }
        Start-Sleep -Milliseconds 700
    }
    return $false
}
# copie de siguranță a configurației și a bazei de joburi (ultimele 3 se păstrează)
function Backup-Data {
    $dir = Join-Path $plan.data_dir ('backup\' + $plan.old_version + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Force $dir | Out-Null
    foreach ($f in 'config.json', 'jobs.sqlite3') { $p = Join-Path $plan.data_dir $f; if (Test-Path $p) { Copy-Item $p $dir -Force } }
    Get-ChildItem (Join-Path $plan.data_dir 'backup') -Directory | Sort-Object Name -Descending | Select-Object -Skip 3 | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    return $dir
}
function Restore-Data($dir) {
    foreach ($f in 'config.json', 'jobs.sqlite3') { $p = Join-Path $dir $f; if (Test-Path $p) { Copy-Item $p (Join-Path $plan.data_dir $f) -Force } }
}
# ce spune versiunea nouă când e pornită în prim-plan (agentul oprit): arată motivul real al eșecului
function Capture-Reason {
    $o = Join-Path $env:TEMP 'wfd-new-out.txt'; $e = Join-Path $env:TEMP 'wfd-new-err.txt'
    Remove-Item $o, $e -ErrorAction SilentlyContinue
    $proc = Start-Process $plan.python -ArgumentList '-E', '-s', '-X', 'utf8', '-m', 'worldbit_fiscal', 'run' -WorkingDirectory $plan.install_dir `
        -RedirectStandardOutput $o -RedirectStandardError $e -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 8
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    $linii = @(Get-Content $e -Encoding UTF8 -ErrorAction SilentlyContinue) + @(Get-Content $o -Encoding UTF8 -ErrorAction SilentlyContinue) | Where-Object { $_ -and $_.Trim() } | Select-Object -Last 14
    return ($linii -join "`n")
}
"" | Out-File $log -Encoding utf8
Remove-Item $resFile -ErrorAction SilentlyContinue
$pkg = Join-Path $plan.install_dir 'worldbit_fiscal'
$bak = Join-Path $plan.install_dir 'worldbit_fiscal.bak'
$backup = $null
try {
    Start-Sleep -Seconds 2
    Stop-Agent
    $backup = Backup-Data
    Say "copie de siguranță: $backup"
    if (Test-Path $bak) { Remove-Item $bak -Recurse -Force }
    Rename-Item $pkg $bak
    Copy-Item $plan.new_pkg $pkg -Recurse -Force
    Start-ScheduledTask -TaskName $plan.task
    if (Wait-Health) {
        Say "OK: actualizat de la $($plan.old_version) la $($plan.version)"
        Finish $true ''
    } else {
        Say "EȘEC: versiunea $($plan.version) nu a răspuns în 40 s; se revine la $($plan.old_version)"
        Stop-Agent
        $motiv = Capture-Reason
        if ($motiv) { Say "MOTIV (ce a scris versiunea nouă la pornire): $motiv" } else { Say "MOTIV: versiunea nouă nu a scris nimic; verifică jurnalul agentului" }
        Remove-Item $pkg -Recurse -Force -ErrorAction SilentlyContinue
        Rename-Item $bak $pkg
        if ($backup) { Restore-Data $backup }
        Start-ScheduledTask -TaskName $plan.task
        Say "Revenit la $($plan.old_version)"
        Finish $false $motiv
    }
} catch {
    Say "EROARE: $_"
    if ((Test-Path $bak) -and -not (Test-Path $pkg)) { Rename-Item $bak $pkg }
    if ($backup) { Restore-Data $backup }
    Start-ScheduledTask -TaskName $plan.task
    Say "Revenit la $($plan.old_version)"
    Finish $false "$_"
}
schtasks /delete /tn $plan.update_task /f | Out-Null
'''


def update_dir(home):
    return Path(home) / "update"


def last_log(home):
    try:
        return (update_dir(home) / "last.txt").read_text(encoding="utf-8-sig").strip() or None
    except OSError:
        return None


def update_result(home):
    """Rezultatul ultimei instalări ({ok, from, to, reason, at}), scris de scriptul de instalare; None dacă nu există."""
    import json
    try:
        return json.loads((update_dir(home) / "result.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _stage_and_launch(new_pkg, home, version, old_version, port):
    import json
    upd = update_dir(home)
    shutil.rmtree(upd, ignore_errors=True)
    upd.mkdir(parents=True)
    shutil.copytree(new_pkg, upd / "worldbit_fiscal", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (upd / "update.ps1").write_text(_PS, encoding="utf-8-sig")
    (upd / "plan.json").write_text(json.dumps({
        "install_dir": str(installed_package().parent), "new_pkg": str(upd / "worldbit_fiscal"), "task": TASK, "update_task": UPDATE_TASK,
        "port": port, "version": version, "old_version": old_version, "python": python_exe(), "data_dir": str(home)}), encoding="utf-8")
    script = str(upd / "update.ps1")
    comanda = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File {chr(34) + script + chr(34) if " " in script else script}'
    for args in (["schtasks", "/create", "/tn", UPDATE_TASK, "/tr", comanda, "/sc", "once", "/st", "00:00", "/ru", "SYSTEM", "/rl", "HIGHEST", "/f"],
                 ["schtasks", "/run", "/tn", UPDATE_TASK]):
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0:
            raise UpdateError(f"nu s-a putut porni actualizarea ({' '.join(args[:2])}): {(r.stdout + r.stderr).strip()}")


def run_update_info(cfg, runner, home):
    """Tot ce face butonul: descarcă, verifică, instalează dacă e cazul. Întoarce {"state", "message", "target"}; nu ridică erori.
    state: current | irrelevant | started (target = versiunea care se instalează) | unavailable | busy | error."""
    url = cfg.get("update_url") or DEFAULT_URL
    work = tempfile.mkdtemp(prefix="wfd-update-")

    def rez(state, message, target=None, detail=None):
        return {"state": state, "message": message, "target": target, "detail": detail}
    try:
        try:
            zip_path = os.path.join(work, "release.zip")
            download(url, zip_path)
            new_pkg = extract(zip_path, os.path.join(work, "x"))
            state, text = decide(installed_package(), new_pkg, module_files(cfg))
        except (OSError, UpdateError) as e:
            return rez("error", f"Nu s-a putut verifica actualizarea: {e}")
        if state != "update":
            return rez(state, text)
        target = fmt(read_version(new_pkg))
        try:
            preflight(new_pkg, target)
        except UpdateError as e:
            return rez("error", f"Versiunea {target} nu a fost instalată: {e}. Agentul actual nu a fost atins.", detail=e.detail)
        ok, why = can_install()
        if not ok:
            return rez("unavailable", f"{text} Nu se poate instala de aici: {why}.")
        if runner.store.unfinished() or not runner.send_lock.acquire(blocking=False):
            return rez("busy", f"{text} Sunt operațiuni fiscale în curs; încearcă după ce se termină.")
        runner.send_lock.release()      # nicio operațiune în zbor chiar acum; instalarea așteaptă oricum 2 secunde înainte să oprească agentul
        try:
            _stage_and_launch(new_pkg, home, target, __version__, cfg["port"])
        except (OSError, UpdateError) as e:
            return rez("error", f"{text} Nu s-a putut porni instalarea: {e}")
        return rez("started", f"{text} Instalarea a pornit: agentul se oprește și repornește singur (aproximativ 30 de secunde). Reîncarcă pagina.", target)
    except Exception as e:
        log.exception("actualizare eșuată")
        return rez("error", f"Actualizarea a eșuat: {e}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_update(cfg, runner, home):
    """La fel, dar întoarce doar textul pentru admin (pagina fără JavaScript)."""
    return run_update_info(cfg, runner, home)["message"]
