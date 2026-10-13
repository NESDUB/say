# Windows bootstrap. Run: powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = 'Stop'
$Installer = Join-Path $PSScriptRoot 'install.py'
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $Installer @args
    exit $LASTEXITCODE
}
if (Get-Command python -ErrorAction SilentlyContinue) {
    & python --version 2>$null
    if ($LASTEXITCODE -eq 0) {
        & python $Installer @args
        exit $LASTEXITCODE
    }
}
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw 'Install Python 3.12 from python.org, then run this installer again.'
}
& winget install --id Python.Python.3.12 --exact --scope user --accept-package-agreements --accept-source-agreements
if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
$Python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if (-not (Test-Path $Python)) { throw 'Open a new PowerShell and rerun install.ps1 so Python is on PATH.' }
& $Python $Installer @args
exit $LASTEXITCODE
