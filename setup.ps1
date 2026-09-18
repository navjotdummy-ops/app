# One-time setup for Windows. In PowerShell, inside this folder, run:  .\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Write-Host "1/4 Creating the private Python folder (.venv)..."
python -m venv .venv
Write-Host "2/4 Installing the two libraries..."
.\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
Write-Host "3/4 Downloading the browser (about 150 MB, one time)..."
.\.venv\Scripts\playwright.exe install chromium
Write-Host "4/4 Checking for the Google key..."
if (Test-Path "service-account.json") {
  $email = .\.venv\Scripts\python.exe -c "import json;print(json.load(open('service-account.json'))['client_email'])"
  Write-Host "    Found service-account.json. Share the sheet (as Editor) with:"
  Write-Host "    $email"
} else {
  Write-Host "    service-account.json is not here yet. Follow README.md Part 2, then re-run: .\setup.ps1"
}
Write-Host ""
Write-Host "Setup done. Next:  .\run_tracker.bat --dry-run --limit 2"
