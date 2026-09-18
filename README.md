# Instagram Reel Stats Tracker

A small script that, once a day, opens each reel from your Google Sheet in a
browser where you are logged in to Instagram, reads **Views, Likes and
Comments**, and writes them back to the sheet. It also adds one line per reel
to a **History** tab so you can see how numbers change over time.

What it never does:

- It never asks for or stores your Instagram password. You log in once by
  hand in a browser window, and that browser session is saved in the project
  folder for next time.
- It never likes, comments, follows, or clicks anything on Instagram. It only
  reads.
- It only writes to the **Views, Likes, Comments, Last Updated and Status**
  columns of your first tab, and only **adds** rows to History. Nothing else
  is changed or deleted.

Everything below is written for someone who has never used a terminal. Each
step says exactly what to type. Budget about 30 minutes the first time.

---

## Part 1. Get the project onto your computer

### 1.1 Open a terminal

- **Mac:** press `Cmd + Space`, type `Terminal`, press Enter.
- **Windows:** press the Windows key, type `PowerShell`, press Enter.

A black or white window with a blinking cursor appears. You type commands
into it and press Enter to run them.

### 1.2 Install Python (skip if you already have it)

- Go to https://www.python.org/downloads/ and download the latest version.
- **Windows only:** on the first screen of the installer, tick the box
  **"Add python.exe to PATH"** before clicking Install.
- Check it worked. In the terminal type:

```
python3 --version
```

(On Windows type `python --version`.) You should see something like
`Python 3.12.4`. Any version 3.10 or newer is fine.

### 1.3 Download the project

If you have Git installed:

```
git clone https://github.com/navjotdummy-ops/app.git reel-stats
cd reel-stats
```

If you do not: on the GitHub page click the green **Code** button, then
**Download ZIP**, unzip it somewhere easy such as your Documents folder,
rename the folder to `reel-stats`, then in the terminal type:

```
cd ~/Documents/reel-stats
```

(Windows: `cd $HOME\Documents\reel-stats`.) From here on, every command
assumes the terminal is "inside" this folder.

### 1.4 Install the two libraries the script needs

The quick way is one command. Mac: `bash setup.sh`. Windows (PowerShell):
`.\setup.ps1`. It creates the private folder, installs the libraries,
downloads the browser, and prints the robot email once the key file is in
place. If you prefer to see each step, here they are by hand.

Mac:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Windows (PowerShell):

```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

If PowerShell refuses to run `Activate.ps1`, run this once and try again:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

What this does: it creates a private folder called `.venv` that holds the
libraries for this project only, then installs `gspread` (talks to Google
Sheets) and `playwright` (drives a browser), then downloads the browser.

Whenever you open a new terminal later, run the `activate` line again before
running the script, or just use `run_tracker.sh` / `run_tracker.bat`, which do
it for you.

---

## Part 2. Give the script access to your Google Sheet

Google does not let a script use your personal login. Instead you create a
**service account**: a robot Google user with its own email address. You share
the sheet with that email exactly like you would share it with a colleague.

### 2.1 Create a Google Cloud project

1. Go to https://console.cloud.google.com/ and sign in with the Google
   account that owns the sheet.
2. At the top, click the project drop-down (it may say "Select a project"),
   then **New Project**.
3. Name it `reel-stats` and click **Create**. Wait a few seconds, then make
   sure `reel-stats` is selected in the drop-down.

### 2.2 Turn on the Google Sheets API

1. In the left menu choose **APIs & Services** then **Library**.
2. Search for **Google Sheets API**, click it, click **Enable**.

### 2.3 Create the service account

1. Left menu: **APIs & Services** then **Credentials**.
2. Click **+ Create credentials** at the top and choose **Service account**.
3. Name: `reel-stats-bot`. Click **Create and continue**, then **Continue**,
   then **Done**. (No roles are needed.)
4. You are back on the Credentials page. Under **Service Accounts**, click the
   email address that ends in `@reel-stats-….iam.gserviceaccount.com`.
5. Open the **Keys** tab, click **Add key**, then **Create new key**, choose
   **JSON**, click **Create**. A file downloads.
6. Move that downloaded file into the project folder and rename it to exactly:

```
service-account.json
```

This file is the robot's password. Keep it private. It is already listed in
`.gitignore`, so it will never be uploaded if you push the project to GitHub.

### 2.4 Share the sheet with the robot

1. Open `service-account.json` in any text editor and copy the value next to
   `"client_email"`. It looks like
   `reel-stats-bot@reel-stats-123456.iam.gserviceaccount.com`.
2. Open your Google Sheet:
   https://docs.google.com/spreadsheets/d/1nr2tn1tVPsjmElTbRXlZPHW_NbMVkpTfzMtkHryvePs/edit
3. Click **Share** (top right), paste that email, set it to **Editor**, untick
   "Notify people", click **Share**.

### 2.5 Check the sheet layout

Row 1 of the first tab must contain these headers (any order, any column;
capitalisation and spaces do not matter):

`Reel Link`, `Creator Name`, `Handle`, `Views`, `Likes`, `Comments`,
`UTM Key`, `Signups`, `Last Updated`, `Status`

The script finds columns by their header names. It will stop with a clear
message if `Reel Link`, `Views`, `Likes`, `Comments`, `Last Updated` or
`Status` is missing. It never adds columns on its own.

Handles can be written with or without the `@`.

---

## Part 3. First run and Instagram login

Make sure the terminal is in the project folder and the `.venv` is activated
(you see `(.venv)` at the start of the line). Then:

```
python reel_stats.py --dry-run --limit 2
```

- `--dry-run` means print the numbers but write **nothing** to the sheet.
- `--limit 2` means only the first 2 reels.

What happens:

1. A Chrome window opens on instagram.com. Log in the normal way (username,
   password, 2-step code if you use one). The script cannot see what you
   type; it only waits until Instagram reports that you are logged in.
2. If Instagram asks "Save your login info?" you can click **Save** or
   **Not now**, either is fine. The session is saved in the `browser_profile`
   folder either way.
3. The script visits the first reel, waits 8 to 15 seconds, visits the
   second, then prints a table like:

```
Row  Handle                    Views      Likes  Comments  Status
2    creator_one              15,342        812        44  OK
3    creator_two              12,300     Hidden         9  OK (approx); views from Reels tab
Updated: 2   Failed: 0   Skipped: 0
```

Compare these with what you see on Instagram. When they match, do the real
run on the same two rows:

```
python reel_stats.py --limit 2
```

Open the sheet: the Views, Likes, Comments, Last Updated and Status cells of
those two rows are filled in, and a **History** tab has two new lines.

To run every row:

```
python reel_stats.py
```

### Where the numbers come from (and what "approx" means)

Instagram sends the exact figures to the browser as hidden data
(`play_count`, `like_count`, `comment_count` and similar). The script reads
those first, so the numbers are exact. Only if that data is missing does it
fall back to the visible text, where Instagram rounds ("1.2K"); in that case
the Status says **approx**. If views are not on the reel page at all, it opens
the creator's Reels tab (`instagram.com/<handle>/reels/`) and reads the number
from the reel's tile there.

If a creator has hidden their like count and Instagram does not send a
number, Likes is written as **Hidden**.

---

## Part 4. Run it every day at 10:00

You have two options.

**Option A: your computer's own scheduler (recommended).** Mac, Windows and
Linux all have a built-in timer that can start a program at a fixed time.
It works whether or not any other app is open. The only requirement is that
the computer is on and you are logged in at 10:00 (the screen can be locked).
Because Instagram must see a normal browser, the script opens a visible
Chrome window for a minute or two and then closes it.

**Option B: a Claude Code Desktop scheduled task.** Claude Code on your
desktop can run a saved prompt on a schedule, and that prompt can run this
script. This works, but it depends on the Claude desktop app being open at
10:00 and adds a layer that can fail on its own. Cloud-side schedules do not
work at all here, because the Instagram login lives in the browser profile on
your computer, not in the cloud.

To set up **Option A**, run once, from the project folder:

```
python schedule_daily.py
```

That installs a daily 10:00 job using launchd (Mac), Task Scheduler
(Windows) or cron (Linux), pointing at `run_tracker.sh` or `run_tracker.bat`
in this folder. Useful variations:

```
python schedule_daily.py --time 09:30         # a different time
python schedule_daily.py --time 12:00,19:00   # twice a day
python schedule_daily.py --show               # see what is installed
python schedule_daily.py --remove             # uninstall
```

Each run writes its own file in the `logs/` folder, named by date and time,
so you can always check what happened.

To set up **Option B** instead: open Claude Code Desktop in this project
folder and say "create a scheduled task that runs `./run_tracker.sh` (or
`run_tracker.bat` on Windows) every day at 10:00". It will create the task
for you and show it in its scheduled-tasks list.

---

## What the Status column means

| Status | Meaning | Numbers |
| --- | --- | --- |
| `OK` | Exact numbers read | Updated |
| `OK (approx); ...` | Some numbers came from rounded visible text | Updated |
| `OK; views from Reels tab` | Views were read from the creator's Reels grid | Updated |
| `OK; likes hidden` | Creator hides likes; Likes cell says Hidden | Updated |
| `Not available (deleted or private)` | Instagram says the page is gone | Old numbers kept |
| `Private account` | The account is private and you do not follow it | Old numbers kept |
| `Failed to load: ...` | Timeout or network problem for this one reel | Old numbers kept |
| `Duplicate of row N` | Same reel link appears earlier in the sheet | Skipped |
| `Invalid link` | Not an Instagram reel/post link | Skipped |
| `Login needed` | Instagram logged you out; the run stopped | Old numbers kept |
| `Blocked` | Instagram showed "try again later" or a checkpoint; the run stopped | Old numbers kept |

When you see `Login needed`, run `python reel_stats.py --login`, log in
again in the window, and the run continues. The same command lets you
switch to a different Instagram account: it forgets the saved session and
shows the login page again.

When you see `Blocked`, wait a few hours. Instagram rate-limits accounts that
open many pages quickly; the 8 to 15 second pause keeps this rare. Opening
Instagram normally in the tracker's browser (`python reel_stats.py --login`)
and scrolling for a minute usually clears it.

---

## Troubleshooting

- **"Service account key not found"**: the file must be named exactly
  `service-account.json` and sit next to `reel_stats.py`.
- **"Google Sheets refused the connection"**: you skipped sharing the sheet
  with the robot email (step 2.4) or enabling the Sheets API (step 2.2).
- **"These column headers are missing"**: check the spelling in row 1 of the
  first tab.
- **The browser opens but Instagram shows the login page every day**: your
  session expired. Run `python reel_stats.py --login` once.
- **Numbers differ slightly from the Instagram app**: Instagram itself shows
  different totals in different places (the app, the web, Insights). The
  script logs every raw field it finds (run with `--verbose` or open the
  log file) so you can see exactly which one was used.
- **Nothing ran at 10:00**: the computer was off or asleep, or you were not
  logged in. Run `python schedule_daily.py --show` to confirm the job is
  installed, and look in `logs/` for `scheduler.out.log` / `scheduler.err.log`.

## Files in this folder

| File | Purpose |
| --- | --- |
| `reel_stats.py` | The tracker itself |
| `schedule_daily.py` | Installs or removes the daily 10:00 schedule |
| `run_tracker.sh`, `run_tracker.bat` | Tiny launchers used by the schedule |
| `setup.sh`, `setup.ps1` | One-command install of everything in Part 1.4 |
| `requirements.txt` | The two libraries to install |
| `tests/test_parsing.py` | Automated checks of the number-parsing logic (`python -m unittest`) |
| `service-account.json` | Your Google key. Not in Git. You create it in step 2.3 |
| `browser_profile/` | Your saved Instagram session. Not in Git. Created on first run |
| `logs/` | One log file per run |

## All command-line options

```
python reel_stats.py [--dry-run] [--limit N] [--login] [--headless]
                     [--sheet-id ID] [--key-file PATH] [--profile-dir PATH]
                     [--min-delay 8] [--max-delay 15] [--verbose]
```

`--headless` runs the browser invisibly. Instagram is more likely to block
invisible browsers, so the default is a visible window.
