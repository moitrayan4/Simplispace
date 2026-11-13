# One-time developer setup for Simplispace on Windows PowerShell.
# Creates the Python venv (mail\), installs Python and frontend packages,
# and creates backend\.env with a fresh encryption key if it doesn't exist.
$ErrorActionPreference = "Stop"
$ROOT = $PSScriptRoot
if (-not $ROOT) { $ROOT = (Get-Location).Path }
$PY = "$ROOT\mail\Scripts\python.exe"

Write-Host "[1/3] Python packages..."
if (-not (Test-Path $PY)) { python -m venv "$ROOT\mail" }
& $PY -m pip install --upgrade pip
& $PY -m pip install -r "$ROOT\requirements.txt"

Write-Host "[2/3] Frontend packages..."
Set-Location "$ROOT\frontend"
npm install
Set-Location "$ROOT"

Write-Host "[3/3] backend\.env..."
$envFile = "$ROOT\backend\.env"
if (Test-Path $envFile) {
    Write-Host "  exists, left unchanged"
} else {
    $key = & $PY -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    $text = (Get-Content "$ROOT\backend\.env.example" -Raw) -replace '(?m)^FERNET_KEY=.*$', "FERNET_KEY=$key"
    [IO.File]::WriteAllText($envFile, $text, (New-Object Text.UTF8Encoding $false))
    Write-Host "  created; now fill in GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET"
}
Write-Host "Done. Run the app:  mail\Scripts\python.exe desktop\main.py  (after: cd frontend; npm run build)"
