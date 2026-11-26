# Krypt Marketo installer for Windows. Run in PowerShell from this folder:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "Installing FFmpeg (free video engine)..."
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Python 3.12..."
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
    Write-Host "Python installed. Close this window, open a new PowerShell, and run install.ps1 again."
    exit 0
}

if (-not (Test-Path .venv)) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    $secret = -join ((48..57) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ })
    (Get-Content .env) -replace '^KRYPT_SESSION_SECRET=.*', "KRYPT_SESSION_SECRET=$secret" | Set-Content .env
    Write-Host "Created .env. Fill in your keys (see SETUP.md)."
}

$answer = Read-Host "Start Krypt Marketo automatically when you log in to Windows? (y/n)"
if ($answer -eq "y") {
    $bat = Join-Path $PSScriptRoot "start.bat"
    schtasks /Create /TN "KryptMarketo" /TR "`"$bat`"" /SC ONLOGON /RL LIMITED /F | Out-Null
    Write-Host "Added to startup (Task Scheduler > KryptMarketo)."
}
Write-Host "Done. Run start.bat, then open http://localhost:8787"
