<#
.SYNOPSIS
  Instalează agentul "driver fiscal worldbit" pe un PC cu casă de marcat și pre-aprobă permanent aplicația în Chrome.

.EXAMPLE
  .\install.ps1 -Driver fisco -Origin https://test.worldbit.ro
  .\install.ps1 -Driver fiscool -Origin https://test.worldbit.ro -Port 4747

  Rulează ca administrator (se re-lansează singur ridicat). Parola paginii /setari se cere la instalare.
  VERIFICAT pe Windows 11 (VM): instalare curată (inclusiv Python), pornire automată la boot ca task SYSTEM, repornire PC, dezinstalare.
  NEVERIFICAT: efectul politicii Chrome (promptul de acces la rețeaua locală) și driverul Fisco; test pe PC-ul real de casă.
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet('fisco', 'fiscool')][string]$Driver,
    [Parameter(Mandatory = $true)][ValidatePattern('^https?://[A-Za-z0-9.\-]+(:\d{1,5})?$')][string]$Origin,
    [int]$Port = 4747,
    [string]$InstallDir = (Join-Path $env:ProgramFiles 'WorldbitFiscalDriver'),
    [SecureString]$AdminPassword
)
$ErrorActionPreference = 'Stop'
$TaskName = 'WorldbitFiscalDriver'
$PythonVersion = '3.12.9'
$Sursa = Split-Path -Parent $PSScriptRoot

# --- administrator
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $args2 = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Driver', $Driver, '-Origin', $Origin, '-Port', $Port, '-InstallDir', "`"$InstallDir`"")
    Start-Process powershell -Verb RunAs -ArgumentList $args2 -Wait
    exit
}

# --- Python (pentru toți utilizatorii, ca să poată rula și contul SYSTEM)
function Find-Python {
    foreach ($radacina in 'HKLM:\SOFTWARE\Python\PythonCore', 'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore') {
        if (-not (Test-Path $radacina)) { continue }
        foreach ($v in Get-ChildItem $radacina | Sort-Object { [version]($_.PSChildName -replace '-.*$', '') } -Descending) {
            if ([version]($v.PSChildName -replace '-.*$', '') -lt [version]'3.10') { continue }
            $cale = (Get-ItemProperty (Join-Path $v.PSPath 'InstallPath') -ErrorAction SilentlyContinue).'(default)'
            if ($cale -and (Test-Path (Join-Path $cale 'python.exe')) -and (Test-Path (Join-Path $cale 'pythonw.exe'))) { return $cale }
        }
    }
    return $null
}
$pyDir = Find-Python
if (-not $pyDir) {
    Write-Host "Se instalează Python $PythonVersion ..."
    $exe = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"
    Invoke-WebRequest "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe" -OutFile $exe
    # /passive (bară de progres), nu /quiet: testat pe Windows 11, cu /quiet programul de instalare iese cu cod 0 fără să instaleze nimic.
    $p = Start-Process $exe -ArgumentList '/passive', 'InstallAllUsers=1', 'PrependPath=0', 'Include_test=0', 'Include_launcher=0' -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "Instalarea Python a eșuat (cod $($p.ExitCode))." }
    # programul de instalare poate ieși înaintea motorului lui: se așteaptă (până la 5 minute) să apară Python în registru
    foreach ($i in 1..60) {
        $pyDir = Find-Python
        if ($pyDir) { break }
        Start-Sleep -Seconds 5
    }
    if (-not $pyDir) { throw 'Python nu a fost găsit în registru după instalare. Instalează-l manual (3.10+, pentru toți utilizatorii) și rulează din nou.' }
}
$python = Join-Path $pyDir 'python.exe'
$pythonw = Join-Path $pyDir 'pythonw.exe'

# --- oprește varianta veche, copiază fișierele
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -like '*worldbit_fiscal*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
New-Item -ItemType Directory -Force $InstallDir | Out-Null
if (Test-Path (Join-Path $InstallDir 'worldbit_fiscal')) { Remove-Item (Join-Path $InstallDir 'worldbit_fiscal') -Recurse -Force }
Copy-Item (Join-Path $Sursa 'worldbit_fiscal') $InstallDir -Recurse -Force
Get-ChildItem $InstallDir -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force

# --- date (config, baza de joburi) și acces restrâns: doar SYSTEM și administratorii
$date = Join-Path $env:ProgramData 'WorldbitFiscalDriver'
New-Item -ItemType Directory -Force $date | Out-Null
icacls $date /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null

# --- configurare inițială (agent_id, parola /setari, driverul, originea)
if (-not $AdminPassword) { $AdminPassword = Read-Host 'Parola pentru pagina /setari (minim 8 caractere)' -AsSecureString }
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($AdminPassword)
try {
    $env:WORLDBIT_ADMIN_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $env:WORLDBIT_FISCAL_HOME = $date
    # -E -s (nu -I): modul izolat ignoră și directorul curent, deci `-m worldbit_fiscal` nu ar găsi pachetul din $InstallDir (scris doar de administratori)
    Push-Location $InstallDir
    & $python -E -s -m worldbit_fiscal init --driver $Driver --port $Port --origin $Origin
    if ($LASTEXITCODE -ne 0) { throw 'init a eșuat.' }
    # Chrome: aplicația are voie permanent să vorbească cu agentul (fără promptul „Access other apps and services on this device")
    & $python -E -s -m worldbit_fiscal chrome-policy add --origin $Origin
    if ($LASTEXITCODE -ne 0) { throw 'Scrierea politicii Chrome a eșuat.' }
    Pop-Location
} finally {
    Remove-Item Env:\WORLDBIT_ADMIN_PASSWORD -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

# --- pornire automată la boot, ascunsă, repornește la cădere
$actiune = New-ScheduledTaskAction -Execute $pythonw -Argument '-E -s -m worldbit_fiscal run' -WorkingDirectory $InstallDir
$declansator = New-ScheduledTaskTrigger -AtStartup
$setari = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $actiune -Trigger $declansator -Settings $setari -Principal $principal -Force | Out-Null
# SYSTEM nu are variabila utilizatorului: dosarul de date e cel implicit pe Windows (%ProgramData%\WorldbitFiscalDriver), același cu $date
Start-ScheduledTask -TaskName $TaskName

# --- verificare
$adresa = "http://127.0.0.1:$Port/health"
$sanatos = $null
foreach ($i in 1..20) {
    Start-Sleep -Seconds 1
    try { $sanatos = Invoke-RestMethod $adresa -TimeoutSec 3; break } catch { }
}
if (-not $sanatos) { throw "Agentul nu răspunde la $adresa. Verifică Task Scheduler (task: $TaskName)." }
Write-Host ''
Write-Host "Instalat. Agentul răspunde la $adresa"
Write-Host "agent_id: $($sanatos.agent_id)"
Write-Host "Setări: http://127.0.0.1:$Port/setari"
Write-Host "Chrome și Edge: originea $Origin a fost aprobată prin politică. Repornește browserul complet (verifică în chrome://policy sau edge://policy)."
