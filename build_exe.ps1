# Rebuild the Simplispace desktop .exe on Windows PowerShell
$ErrorActionPreference = "Stop"
$ROOT = $PSScriptRoot
if (-not $ROOT) { $ROOT = (Get-Location).Path }
$PY = "$ROOT\mail\Scripts\python.exe"

# 1. Kill any running instances so the .exe isn't file-locked
Get-Process -Name "Simplispace" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
}

# 2. Build frontend
Write-Host "[1/3] Building frontend..."
Set-Location "$ROOT\frontend"
npm run build
Set-Location "$ROOT"

# 3. Build .exe with PyInstaller
Write-Host "[2/3] Building exe (PyInstaller)..."
# Ship only the app's Google OAuth client and the AI server's address, never the
# developer's FERNET_KEY, AI_API_KEY or data.
New-Item -ItemType Directory -Force "$ROOT\build" | Out-Null
Select-String -Path "$ROOT\backend\.env" -Pattern '^(GOOGLE_CLIENT_ID|GOOGLE_CLIENT_SECRET|AI_SERVER_URL)=' |
    ForEach-Object { $_.Line } | Set-Content -Encoding ascii "$ROOT\build\oauth.env"
& $PY -m PyInstaller --noconfirm --clean Simplispace.spec

Write-Host "[3/3] Done: $ROOT\dist\Simplispace.exe (each user's data: %LOCALAPPDATA%\Simplispace)"
