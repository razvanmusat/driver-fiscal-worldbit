<#
.SYNOPSIS
  Dezinstalează agentul. Datele (config, joburi) se păstrează, cu excepția -StergeDate.
.EXAMPLE
  .\uninstall.ps1 -Origin https://test.worldbit.ro
#>
param(
    [string]$Origin,
    [string]$InstallDir = (Join-Path $env:ProgramFiles 'WorldbitFiscalDriver'),
    [switch]$StergeDate
)
$ErrorActionPreference = 'Stop'
$TaskName = 'WorldbitFiscalDriver'
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Rulează ca administrator.'
}
$python = Get-ChildItem 'HKLM:\SOFTWARE\Python\PythonCore\*\InstallPath' -ErrorAction SilentlyContinue |
    ForEach-Object { Join-Path (Get-ItemProperty $_.PSPath).'(default)' 'python.exe' } | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($Origin -and $python -and (Test-Path $InstallDir)) {
    Push-Location $InstallDir
    & $python -E -s -m worldbit_fiscal chrome-policy remove --origin $Origin
    Pop-Location
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -like '*worldbit_fiscal*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
if ($StergeDate) { Remove-Item (Join-Path $env:ProgramData 'WorldbitFiscalDriver') -Recurse -Force -ErrorAction SilentlyContinue }
Write-Host 'Dezinstalat.'
