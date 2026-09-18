#!/usr/bin/env bash
# One-time setup for Mac / Linux. Run:  bash setup.sh
set -e
cd "$(dirname "$0")"
echo "1/4 Creating the private Python folder (.venv)..."
python3 -m venv .venv
echo "2/4 Installing the two libraries..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
echo "3/4 Downloading the browser (about 150 MB, one time)..."
.venv/bin/playwright install chromium
echo "4/4 Checking for the Google key..."
if [ -f service-account.json ]; then
  EMAIL=$(.venv/bin/python -c "import json;print(json.load(open('service-account.json'))['client_email'])")
  echo "    Found service-account.json. Share the sheet (as Editor) with:"
  echo "    $EMAIL"
else
  echo "    service-account.json is not here yet. Follow README.md Part 2, then re-run: bash setup.sh"
fi
echo
echo "Setup done. Next:  ./run_tracker.sh --dry-run --limit 2"
