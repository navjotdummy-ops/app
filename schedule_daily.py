#!/usr/bin/env python3
"""
Installs (or removes) the daily 10:00 AM schedule for reel_stats.py using
your computer's own scheduler. Works on Mac (launchd), Windows (Task
Scheduler) and Linux (cron). Run it once from the project folder:

    python schedule_daily.py                    # install, runs every day at 10:00
    python schedule_daily.py --time 12:00       # a different time
    python schedule_daily.py --time 12:00,19:00 # twice a day
    python schedule_daily.py --show             # print what is installed
    python schedule_daily.py --remove           # uninstall
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LABEL = "com.reelstats.daily"          # Mac launchd job name
TASK_NAME = "ReelStatsTracker"          # Windows Task Scheduler name
CRON_MARKER = "# reelstats-daily"       # Linux crontab marker


def parse_times(text: str) -> list[tuple[int, int]]:
    """'12:00,19:00' -> [(12, 0), (19, 0)]"""
    times: list[tuple[int, int]] = []
    for part in text.split(","):
        part = part.strip()
        try:
            hour, minute = part.split(":")
            hour_i, minute_i = int(hour), int(minute)
            if not (0 <= hour_i <= 23 and 0 <= minute_i <= 59):
                raise ValueError
        except ValueError:
            raise SystemExit(f"Each time must look like 10:00 (24-hour clock), got: {part}")
        if (hour_i, minute_i) not in times:
            times.append((hour_i, minute_i))
    return times


def describe(times: list[tuple[int, int]]) -> str:
    return " and ".join(f"{h:02d}:{m:02d}" for h, m in times)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print("  $", " ".join(cmd))
    try:
        return subprocess.run(cmd, check=check, text=True, capture_output=True)
    except FileNotFoundError:
        raise SystemExit(f"The '{cmd[0]}' command is not available on this computer, "
                         "so the schedule cannot be installed automatically. See README.md, Part 4.")


# ---------------------------------------------------------------- macOS ----

def mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def mac_install(times: list[tuple[int, int]]) -> None:
    script = BASE_DIR / "run_tracker.sh"
    logs = BASE_DIR / "logs"
    logs.mkdir(exist_ok=True)
    intervals = "".join(
        f"""
        <dict>
            <key>Hour</key><integer>{h}</integer>
            <key>Minute</key><integer>{m}</integer>
        </dict>"""
        for h, m in times
    )
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>{script}</string>
    </array>
    <key>WorkingDirectory</key><string>{BASE_DIR}</string>
    <key>StartCalendarInterval</key>
    <array>{intervals}
    </array>
    <key>StandardOutPath</key><string>{logs / 'scheduler.out.log'}</string>
    <key>StandardErrorPath</key><string>{logs / 'scheduler.err.log'}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""
    path = mac_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        run(["launchctl", "unload", str(path)], check=False)
    path.write_text(plist)
    run(["launchctl", "load", "-w", str(path)])
    print(f"\nInstalled. Every day at {describe(times)} your Mac will run:\n  {script}")
    print("If the Mac is asleep at that time, it runs as soon as it wakes up.")
    print("You must be logged in to your Mac (the screen can be locked).")


def mac_remove() -> None:
    path = mac_plist_path()
    if path.exists():
        run(["launchctl", "unload", "-w", str(path)], check=False)
        path.unlink()
        print("Removed the daily schedule.")
    else:
        print("No schedule was installed.")


def mac_show() -> None:
    path = mac_plist_path()
    if not path.exists():
        print("No schedule installed.")
        return
    print(path.read_text())
    out = run(["launchctl", "list"], check=False).stdout
    print("Loaded:", "yes" if LABEL in out else "no (run without --show to load it)")


# -------------------------------------------------------------- Windows ----

def win_task_names() -> list[str]:
    """All tasks we ever created (one per time of day)."""
    result = run(["schtasks", "/Query", "/FO", "CSV", "/NH"], check=False)
    names = []
    for line in result.stdout.splitlines():
        name = line.split(",")[0].strip('"').lstrip("\\")
        if name == TASK_NAME or name.startswith(TASK_NAME + "_"):
            names.append(name)
    return names


def win_install(times: list[tuple[int, int]]) -> None:
    script = BASE_DIR / "run_tracker.bat"
    for old in win_task_names():
        run(["schtasks", "/Delete", "/F", "/TN", old], check=False)
    for h, m in times:
        name = f"{TASK_NAME}_{h:02d}{m:02d}"
        cmd = [
            "schtasks", "/Create", "/F",
            "/SC", "DAILY",
            "/TN", name,
            "/TR", f'"{script}"',
            "/ST", f"{h:02d}:{m:02d}",
        ]
        result = run(cmd, check=False)
        print(result.stdout or result.stderr)
        if result.returncode != 0:
            raise SystemExit("Task Scheduler refused. Try again from a terminal opened as Administrator.")
    print(f"Installed. Every day at {describe(times)} Windows will run:\n  {script}")
    print("It runs only while you are logged in to Windows (so the browser window can open).")
    print("If the PC was off at that time, run it by hand or open Task Scheduler and pick Run.")


def win_remove() -> None:
    names = win_task_names()
    if not names:
        print("No schedule was installed.")
    for name in names:
        result = run(["schtasks", "/Delete", "/F", "/TN", name], check=False)
        print(result.stdout or result.stderr)


def win_show() -> None:
    names = win_task_names()
    if not names:
        print("No schedule installed.")
    for name in names:
        result = run(["schtasks", "/Query", "/TN", name, "/V", "/FO", "LIST"], check=False)
        print(result.stdout or result.stderr)


# ---------------------------------------------------------------- Linux ----

def cron_read() -> str:
    try:
        result = subprocess.run(["crontab", "-l"], text=True, capture_output=True)
    except FileNotFoundError:
        raise SystemExit("The 'crontab' command is not available on this computer. See README.md, Part 4.")
    return result.stdout if result.returncode == 0 else ""


def cron_write(content: str) -> None:
    subprocess.run(["crontab", "-"], input=content, text=True, check=True)


def linux_install(times: list[tuple[int, int]]) -> None:
    script = BASE_DIR / "run_tracker.sh"
    logs = BASE_DIR / "logs"
    logs.mkdir(exist_ok=True)
    display = os.environ.get("DISPLAY", ":0")
    lines = [
        (f"{m} {h} * * * DISPLAY={display} /bin/bash {script} "
         f">> {logs / 'scheduler.log'} 2>&1 {CRON_MARKER}")
        for h, m in times
    ]
    existing = [l for l in cron_read().splitlines() if CRON_MARKER not in l]
    cron_write("\n".join(existing + lines) + "\n")
    print("Installed these cron lines:\n  " + "\n  ".join(lines))


def linux_remove() -> None:
    existing = [l for l in cron_read().splitlines() if CRON_MARKER not in l]
    cron_write("\n".join(existing) + ("\n" if existing else ""))
    print("Removed the daily schedule.")


def linux_show() -> None:
    lines = [l for l in cron_read().splitlines() if CRON_MARKER in l]
    print("\n".join(lines) if lines else "No schedule installed.")


# ----------------------------------------------------------------- main ----

def main() -> int:
    p = argparse.ArgumentParser(description="Install the daily schedule for reel_stats.py")
    p.add_argument("--time", default="10:00",
                   help="Local time(s), 24-hour clock, comma-separated for more than one. Default 10:00")
    p.add_argument("--remove", action="store_true", help="Uninstall the schedule")
    p.add_argument("--show", action="store_true", help="Show the installed schedule")
    args = p.parse_args()
    times = parse_times(args.time)

    system = platform.system()
    table = {
        "Darwin": (mac_install, mac_remove, mac_show),
        "Windows": (win_install, win_remove, win_show),
        "Linux": (linux_install, linux_remove, linux_show),
    }
    if system not in table:
        raise SystemExit(f"Unsupported operating system: {system}")
    install, remove, show = table[system]

    if not (BASE_DIR / "reel_stats.py").exists():
        raise SystemExit("Run this from the project folder that contains reel_stats.py")

    if args.show:
        show()
    elif args.remove:
        remove()
    else:
        install(times)
    return 0


if __name__ == "__main__":
    sys.exit(main())
