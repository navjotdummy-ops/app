#!/usr/bin/env python3
"""
Daily Instagram Reel stats tracker.

Reads reel links from a Google Sheet, opens each reel in a browser where you
are already logged in to Instagram, reads Views / Likes / Comments, and writes
them back to the sheet (plus one row per reel in a "History" tab).

Read-only on Instagram: it never likes, comments, follows or clicks anything
that changes your account. Your Instagram password is never asked for or
stored; you log in once by hand in the browser window and the session is kept
in the browser_profile/ folder.

Usage examples (see README.md for the plain-language guide):

    python reel_stats.py --dry-run --limit 2   # test on the first 2 rows, no writes
    python reel_stats.py --limit 2             # real run on the first 2 rows
    python reel_stats.py                       # real run on every row (daily job)
    python reel_stats.py --login               # force the manual login window
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# Settings you may want to change
# ---------------------------------------------------------------------------

# The ID is the long code in the sheet's URL, between /d/ and /edit
DEFAULT_SHEET_ID = "1nr2tn1tVPsjmElTbRXlZPHW_NbMVkpTfzMtkHryvePs"

# Column headers in the first tab (matched by name, case and spaces ignored)
COL_LINK = "Reel Link"
COL_CREATOR = "Creator Name"
COL_HANDLE = "Handle"
COL_VIEWS = "Views"
COL_LIKES = "Likes"
COL_COMMENTS = "Comments"
COL_SHARES = "Shares"          # optional: filled only if this header exists
COL_UPDATED = "Last Updated"
COL_STATUS = "Status"

HISTORY_TAB = "History"
HISTORY_HEADERS = ["Date", "Reel Link", "Views", "Likes", "Comments", "Shares"]

MIN_DELAY_SECONDS = 8
MAX_DELAY_SECONDS = 15

# Everything below lives next to this script, so it works no matter which
# folder the scheduler starts it from.
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_KEY_FILE = BASE_DIR / "service-account.json"
DEFAULT_PROFILE_DIR = BASE_DIR / "browser_profile"
LOG_DIR = BASE_DIR / "logs"

LOGIN_WAIT_MINUTES = 10
PAGE_TIMEOUT_MS = 45_000

# ---------------------------------------------------------------------------
# Status texts written to the sheet
# ---------------------------------------------------------------------------

STATUS_OK = "OK"
STATUS_OK_APPROX = "OK (approx)"
STATUS_LOGIN_NEEDED = "Login needed"
STATUS_BLOCKED = "Blocked"
STATUS_NOT_AVAILABLE = "Not available (deleted or private)"
STATUS_PRIVATE = "Private account"
STATUS_INVALID_LINK = "Invalid link"

log = logging.getLogger("reel_stats")


# ---------------------------------------------------------------------------
# Small helpers (pure functions, covered by tests/)
# ---------------------------------------------------------------------------

def normalize_header(text: str) -> str:
    """'  Reel Link ' -> 'reellink' so header matching is forgiving."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


_SHORTCODE_RE = re.compile(r"instagram\.com/(?:[^/]+/)?(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)")


def shortcode_from_url(url: str) -> Optional[str]:
    """Pull the reel's short code out of any Instagram post/reel URL."""
    if not url:
        return None
    m = _SHORTCODE_RE.search(url.strip())
    return m.group(1) if m else None


def canonical_reel_url(shortcode: str) -> str:
    return f"https://www.instagram.com/reel/{shortcode}/"


_COUNT_RE = re.compile(r"^\s*([0-9][0-9,.]*)\s*([KkMmBb])?\s*$")


def parse_count(text: str) -> tuple[Optional[int], bool]:
    """
    Turn visible text like '1,234', '1.2K', '3.4M' into a number.
    Returns (number, is_approximate). K/M/B suffixes are approximate.
    """
    if text is None:
        return None, False
    m = _COUNT_RE.match(str(text).replace(" ", " "))
    if not m:
        return None, False
    number, suffix = m.group(1), m.group(2)
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return None, False
    if suffix:
        mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[suffix.lower()]
        return int(round(value * mult)), True
    return int(value), False


@dataclass
class ReelStats:
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    likes_hidden: bool = False
    approx: bool = False
    owner_handle: Optional[str] = None
    owner_name: Optional[str] = None
    media_id: Optional[str] = None
    sources: list[str] = field(default_factory=list)
    raw_fields: dict[str, Any] = field(default_factory=dict)

    def merge_missing(self, other: "ReelStats") -> None:
        """Fill in anything we do not have yet from another result."""
        took_something = False
        if self.views is None and other.views is not None:
            self.views = other.views
            took_something = True
            if other.approx:
                self.approx = True
        if self.likes is None and other.likes is not None:
            self.likes = other.likes
            took_something = True
            if other.approx:
                self.approx = True
        if self.comments is None and other.comments is not None:
            self.comments = other.comments
            took_something = True
            if other.approx:
                self.approx = True
        if self.shares is None and other.shares is not None:
            self.shares = other.shares
            took_something = True
        if other.likes_hidden and not self.likes_hidden:
            self.likes_hidden = True
            took_something = True
        if self.owner_handle is None and other.owner_handle:
            self.owner_handle = other.owner_handle
        if self.owner_name is None and other.owner_name:
            self.owner_name = other.owner_name
        if self.media_id is None and other.media_id:
            self.media_id = other.media_id
        if took_something:
            self.sources.extend(s for s in other.sources if s not in self.sources)
        for k, v in other.raw_fields.items():
            self.raw_fields.setdefault(k, v)

    @property
    def complete(self) -> bool:
        return (
            self.views is not None
            and (self.likes is not None or self.likes_hidden)
            and self.comments is not None
        )


# Field names Instagram uses for each metric, in order of preference.
VIEW_KEYS = ("play_count", "ig_play_count", "view_count", "video_play_count", "video_view_count")
LIKE_KEYS = ("like_count",)
COMMENT_KEYS = ("comment_count",)
SHARE_KEYS = ("reshare_count", "share_count")
LIKE_NESTED = (("edge_media_preview_like", "count"), ("edge_liked_by", "count"))
COMMENT_NESTED = (("edge_media_to_parent_comment", "count"), ("edge_media_to_comment", "count"))


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _iter_media_nodes(obj: Any, shortcode: str) -> Iterable[dict]:
    """Walk any JSON structure and yield dicts that describe this reel."""
    stack = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if current.get("code") == shortcode or current.get("shortcode") == shortcode:
                yield current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def extract_stats_from_json(data: Any, shortcode: str, source: str = "json") -> Optional[ReelStats]:
    """Find exact counts for one reel inside any Instagram JSON blob."""
    result: Optional[ReelStats] = None
    for node in _iter_media_nodes(data, shortcode):
        stats = ReelStats(sources=[source])
        for key in VIEW_KEYS:
            v = _as_int(node.get(key))
            if v is not None:
                stats.raw_fields[key] = v
                if stats.views is None:
                    stats.views = v
        for key in LIKE_KEYS:
            v = _as_int(node.get(key))
            if v is not None:
                stats.raw_fields[key] = v
                if stats.likes is None:
                    stats.likes = v
        for parent, child in LIKE_NESTED:
            sub = node.get(parent)
            if isinstance(sub, dict):
                v = _as_int(sub.get(child))
                if v is not None:
                    stats.raw_fields[f"{parent}.{child}"] = v
                    if stats.likes is None:
                        stats.likes = v
        for key in COMMENT_KEYS:
            v = _as_int(node.get(key))
            if v is not None:
                stats.raw_fields[key] = v
                if stats.comments is None:
                    stats.comments = v
        for parent, child in COMMENT_NESTED:
            sub = node.get(parent)
            if isinstance(sub, dict):
                v = _as_int(sub.get(child))
                if v is not None:
                    stats.raw_fields[f"{parent}.{child}"] = v
                    if stats.comments is None:
                        stats.comments = v
        for key in SHARE_KEYS:
            v = _as_int(node.get(key))
            if v is not None:
                stats.raw_fields[key] = v
                if stats.shares is None:
                    stats.shares = v
        for key, v in node.items():
            if ("share" in key or "save" in key) and key not in stats.raw_fields and _as_int(v) is not None:
                stats.raw_fields[key] = _as_int(v)
        if node.get("like_and_view_counts_disabled") is True and stats.likes is None:
            stats.likes_hidden = True
        pk = node.get("pk") or node.get("id")
        if pk is not None:
            pk = str(pk).split("_")[0]
            if pk.isdigit():
                stats.media_id = pk
        for owner_key in ("owner", "user"):
            owner = node.get(owner_key)
            if isinstance(owner, dict) and owner.get("username"):
                stats.owner_handle = str(owner["username"]).lstrip("@")
                full_name = owner.get("full_name")
                if isinstance(full_name, str) and full_name.strip():
                    stats.owner_name = full_name.strip()
                break
        if not (stats.views is not None or stats.likes is not None or stats.comments is not None
                or stats.likes_hidden or stats.owner_handle or stats.media_id):
            continue
        if result is None:
            result = stats
        else:
            result.merge_missing(stats)
    return result


_TEXT_PATTERNS = {
    "likes": re.compile(r"([0-9][0-9,.]*\s*[KkMmBb]?)\s+likes?\b", re.I),
    "comments": re.compile(r"([0-9][0-9,.]*\s*[KkMmBb]?)\s+comments?\b", re.I),
    "views": re.compile(r"([0-9][0-9,.]*\s*[KkMmBb]?)\s+(?:views?|plays?)\b", re.I),
}
_HIDDEN_LIKES_RE = re.compile(r"liked by\s+\S+\s+and\s+others", re.I)


def extract_stats_from_text(text: str) -> ReelStats:
    """Fallback: read the numbers from the visible page text."""
    stats = ReelStats(sources=["page text"])
    text = text or ""
    for name, pattern in _TEXT_PATTERNS.items():
        m = pattern.search(text)
        if not m:
            continue
        value, approx = parse_count(m.group(1))
        if value is None:
            continue
        setattr(stats, name, value)
        if approx:
            stats.approx = True
    if stats.likes is None and _HIDDEN_LIKES_RE.search(text):
        stats.likes_hidden = True
    return stats


def build_status(stats: ReelStats, notes: list[str]) -> str:
    base = STATUS_OK_APPROX if stats.approx else STATUS_OK
    if notes:
        return f"{base}; " + "; ".join(notes)
    return base


# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------

@dataclass
class SheetRow:
    row_number: int          # 1-based row number in the sheet
    link: str
    creator: str
    handle: str
    old_views: str
    old_likes: str
    old_comments: str
    shortcode: Optional[str]


class SheetClient:
    """Thin wrapper around gspread that only ever touches the allowed cells."""

    def __init__(self, key_file: Path, sheet_id: str, dry_run: bool):
        import gspread  # imported here so --help works without it installed

        if not key_file.exists():
            raise SystemExit(
                f"Service account key not found: {key_file}\n"
                "Follow README.md, step 2, to create it and save it in the project folder."
            )
        self.gspread = gspread
        self.dry_run = dry_run
        self.client = gspread.service_account(filename=str(key_file))
        try:
            self.spreadsheet = self.client.open_by_key(sheet_id)
        except gspread.exceptions.APIError as exc:  # pragma: no cover - network
            raise SystemExit(
                "Google Sheets refused the connection. Most common causes:\n"
                "  1. The sheet is not shared with the service account email (README step 2).\n"
                "  2. The Google Sheets API is not enabled for the project.\n"
                f"Details: {exc}"
            ) from exc
        self.ws = self.spreadsheet.get_worksheet(0)
        self.columns: dict[str, int] = {}
        self.rows: list[SheetRow] = []

    # -- reading -----------------------------------------------------------

    def load(self) -> list[SheetRow]:
        values = self.ws.get_all_values()
        if not values:
            raise SystemExit("The first tab of the sheet is empty.")
        headers = values[0]
        lookup = {normalize_header(h): i + 1 for i, h in enumerate(headers) if h.strip()}
        needed = [COL_LINK, COL_VIEWS, COL_LIKES, COL_COMMENTS, COL_UPDATED, COL_STATUS]
        missing = [name for name in needed if normalize_header(name) not in lookup]
        if missing:
            raise SystemExit(
                "These column headers are missing from row 1 of the first tab: "
                + ", ".join(missing)
                + ". Add them (the script will not create columns by itself)."
            )
        for name in needed + [COL_CREATOR, COL_HANDLE, COL_SHARES]:
            col = lookup.get(normalize_header(name))
            if col:
                self.columns[name] = col
        if COL_SHARES not in self.columns:
            # The one header the script adds by itself: "Shares", in the first
            # empty cell of row 1. Nothing else about the layout is touched.
            col = len(headers) + 1
            for i, h in enumerate(headers, start=1):
                if not h.strip():
                    col = i
                    break
            a1 = self.gspread.utils.rowcol_to_a1(1, col)
            if self.dry_run:
                log.info("[dry-run] would add a '%s' header at %s", COL_SHARES, a1)
            else:
                self._with_retry(lambda: self.ws.update(range_name=a1, values=[[COL_SHARES]]))
                log.info("Added a '%s' header at %s.", COL_SHARES, a1)
                self.columns[COL_SHARES] = col
        log.info("Column map: %s", {k: self.gspread.utils.rowcol_to_a1(1, v)[:-1] for k, v in self.columns.items()})

        def cell(row: list[str], name: str) -> str:
            col = self.columns.get(name)
            if not col or col - 1 >= len(row):
                return ""
            return row[col - 1].strip()

        rows: list[SheetRow] = []
        for idx, row in enumerate(values[1:], start=2):
            link = cell(row, COL_LINK)
            if not link:
                continue
            rows.append(
                SheetRow(
                    row_number=idx,
                    link=link,
                    creator=cell(row, COL_CREATOR),
                    handle=cell(row, COL_HANDLE).lstrip("@"),
                    old_views=cell(row, COL_VIEWS),
                    old_likes=cell(row, COL_LIKES),
                    old_comments=cell(row, COL_COMMENTS),
                    shortcode=shortcode_from_url(link),
                )
            )
        self.rows = rows
        return rows

    # -- writing -----------------------------------------------------------

    def _a1(self, row: int, col_name: str) -> str:
        return self.gspread.utils.rowcol_to_a1(row, self.columns[col_name])

    def write_row(self, row: SheetRow, values: dict[str, Any]) -> None:
        """values maps a column name (Views/Likes/...) to the new cell value."""
        allowed = {COL_VIEWS, COL_LIKES, COL_COMMENTS, COL_SHARES, COL_UPDATED, COL_STATUS, COL_CREATOR, COL_HANDLE}
        bad = set(values) - allowed
        if bad:
            raise ValueError(f"Refusing to write to protected columns: {bad}")
        if self.dry_run:
            log.info("[dry-run] row %s would get %s", row.row_number, values)
            return
        payload = [
            {"range": self._a1(row.row_number, name), "values": [[value]]}
            for name, value in values.items()
        ]
        self._with_retry(lambda: self.ws.batch_update(payload, value_input_option="RAW"))

    def append_history(self, entries: list[list[Any]]) -> None:
        if not entries:
            return
        if self.dry_run:
            log.info("[dry-run] would write %d line(s) to '%s'", len(entries), HISTORY_TAB)
            return
        try:
            hist = self.spreadsheet.worksheet(HISTORY_TAB)
        except self.gspread.WorksheetNotFound:
            hist = self.spreadsheet.add_worksheet(title=HISTORY_TAB, rows=1000, cols=max(10, len(HISTORY_HEADERS)))
            hist.update(range_name="A1", values=[HISTORY_HEADERS])
            log.info("Created the '%s' tab.", HISTORY_TAB)
        else:
            first_row = hist.row_values(1)
            if not any(first_row):
                hist.update(range_name="A1", values=[HISTORY_HEADERS])
            elif len([h for h in first_row if h]) < len(HISTORY_HEADERS):
                # An older History tab without the newer columns: widen it if
                # needed and add the missing headers to the right. Existing
                # cells are untouched.
                if hist.col_count < len(HISTORY_HEADERS):
                    self._with_retry(lambda: hist.add_cols(len(HISTORY_HEADERS) - hist.col_count))
                missing = HISTORY_HEADERS[len(first_row):]
                start = self.gspread.utils.rowcol_to_a1(1, len(first_row) + 1)
                self._with_retry(lambda: hist.update(range_name=start, values=[missing]))
                log.info("Added %s to the '%s' tab header.", missing, HISTORY_TAB)

        # One line per reel per day: a later run on the same day overwrites
        # that day's numbers instead of adding a second line. Earlier days are
        # never touched.
        existing = self._with_retry(hist.get_all_values)
        by_key: dict[tuple[str, str], int] = {}
        for idx, row in enumerate(existing[1:], start=2):
            if len(row) >= 2 and row[0] and row[1]:
                by_key.setdefault((row[0].strip(), row[1].strip()), idx)

        updates = []
        new_rows = []
        for entry in entries:
            key = (str(entry[0]).strip(), str(entry[1]).strip())
            row_idx = by_key.get(key)
            if row_idx:
                a1 = f"A{row_idx}:{self.gspread.utils.rowcol_to_a1(row_idx, len(entry))}"
                updates.append({"range": a1, "values": [entry]})
            else:
                new_rows.append(entry)
        if updates:
            self._with_retry(lambda: hist.batch_update(updates, value_input_option="RAW"))
            log.info("Updated %d existing line(s) in '%s' for today.", len(updates), HISTORY_TAB)
        if new_rows:
            # append_rows only ever adds new rows at the bottom; old rows are untouched.
            self._with_retry(lambda: hist.append_rows(new_rows, value_input_option="RAW", table_range="A1"))
            log.info("Added %d new line(s) to '%s'.", len(new_rows), HISTORY_TAB)

    @staticmethod
    def _with_retry(fn, attempts: int = 4):
        delay = 2
        for attempt in range(1, attempts + 1):
            try:
                return fn()
            except Exception as exc:  # gspread APIError, network hiccups
                if attempt == attempts:
                    raise
                log.warning("Sheets write failed (%s). Retrying in %ss...", exc, delay)
                time.sleep(delay)
                delay *= 2


# ---------------------------------------------------------------------------
# Instagram (Playwright)
# ---------------------------------------------------------------------------

class RunStopped(Exception):
    """Raised when the whole run must stop (login needed / blocked)."""

    def __init__(self, status: str, detail: str = ""):
        super().__init__(detail or status)
        self.status = status
        self.detail = detail


class ReelFailed(Exception):
    """Raised when one reel could not be read; the run continues."""

    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


class InstagramBrowser:
    def __init__(self, profile_dir: Path, headless: bool):
        self.profile_dir = profile_dir
        self.headless = headless
        self.playwright = None
        self.context = None
        self.page = None
        self._json_bodies: list[tuple[str, Any]] = []

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        from playwright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.playwright = sync_playwright().start()
        launch_kwargs = dict(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            # Keep Chrome's own sandbox on. Playwright turns it off by default,
            # which makes real Chrome show a grey "unsupported flag" banner.
            chromium_sandbox=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            # Real Chrome, if installed, looks the most like a normal user.
            self.context = self.playwright.chromium.launch_persistent_context(channel="chrome", **launch_kwargs)
            log.info("Browser: Google Chrome (%s)", "headless" if self.headless else "visible")
        except Exception:
            self.context = self.playwright.chromium.launch_persistent_context(**launch_kwargs)
            log.info("Browser: Playwright Chromium (%s)", "headless" if self.headless else "visible")
        self.context.set_default_timeout(PAGE_TIMEOUT_MS)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.on("response", self._on_response)

    def close(self) -> None:
        try:
            if self.context:
                self.context.close()
        finally:
            if self.playwright:
                self.playwright.stop()

    def restart(self, headless: bool) -> None:
        self.close()
        self.headless = headless
        self.start()

    # -- network capture ---------------------------------------------------

    def _on_response(self, response) -> None:
        try:
            url = response.url
            if "instagram.com" not in url:
                return
            if not any(part in url for part in ("/graphql", "/api/v1/", "/api/graphql")):
                return
            ctype = response.headers.get("content-type", "")
            if "json" not in ctype and "javascript" not in ctype:
                return
            body = response.text()
            if not body:
                return
            body = body.removeprefix("for (;;);")
            self._json_bodies.append((url, json.loads(body)))
        except Exception:
            # Response bodies vanish on navigation; that is fine, we have other sources.
            pass

    def _embedded_json(self) -> list[Any]:
        """JSON that Instagram embeds directly in the HTML of the page."""
        blobs: list[Any] = []
        try:
            scripts = self.page.eval_on_selector_all(
                'script[type="application/json"]',
                "nodes => nodes.map(n => n.textContent)",
            )
        except Exception:
            return blobs
        for raw in scripts:
            if not raw or ("count" not in raw and "shortcode" not in raw):
                continue
            try:
                blobs.append(json.loads(raw))
            except Exception:
                continue
        return blobs

    # -- login -------------------------------------------------------------

    def _has_session_cookie(self) -> bool:
        try:
            return any(c.get("name") == "sessionid" and c.get("value") for c in self.context.cookies())
        except Exception:
            return False

    def _login_form_visible(self) -> bool:
        try:
            return self.page.locator('input[name="username"]').count() > 0
        except Exception:
            return False

    def is_logged_in(self) -> bool:
        return self._has_session_cookie() and not self._login_form_visible()

    def ensure_logged_in(self, force_login: bool = False) -> None:
        self.page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)
        if self.is_logged_in() and not force_login:
            log.info("Instagram session found in the saved browser profile.")
            return
        if force_login and self._has_session_cookie():
            # --login means "switch account": forget the saved session first.
            log.info("Clearing the saved Instagram session so you can log in again.")
            self.context.clear_cookies()
            self.page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)
        if self.headless:
            log.info("Not logged in; opening a visible browser window so you can log in.")
            self.restart(headless=False)
            self.page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        print("\n" + "=" * 70)
        print("  Please log in to Instagram in the browser window that just opened.")
        print("  (Use your usual username, password and any 2-step code.)")
        print(f"  The script waits up to {LOGIN_WAIT_MINUTES} minutes, then carries on by itself.")
        print("  Your password is never seen or stored by this script.")
        print("=" * 70 + "\n")
        deadline = time.time() + LOGIN_WAIT_MINUTES * 60
        while time.time() < deadline:
            self.page.wait_for_timeout(2000)
            if self.is_logged_in():
                log.info("Login detected. Session saved to %s", self.profile_dir)
                # Dismiss the "Save your login info?" prompt by moving on; nothing is clicked.
                self.page.wait_for_timeout(3000)
                return
        raise RunStopped(STATUS_LOGIN_NEEDED, "Login was not completed in time.")

    # -- page checks -------------------------------------------------------

    def _check_for_blockers(self) -> None:
        url = self.page.url or ""
        if "/accounts/login" in url or self._login_form_visible():
            raise RunStopped(STATUS_LOGIN_NEEDED, "Instagram showed the login page.")
        if "/challenge/" in url or "/checkpoint/" in url or "/accounts/suspended" in url:
            raise RunStopped(STATUS_BLOCKED, "Instagram asked for a security checkpoint.")
        try:
            text = self.page.inner_text("body", timeout=5000)
        except Exception:
            text = ""
        lowered = text.lower()
        for marker in ("try again later", "please wait a few minutes", "we limit how often",
                       "something went wrong", "suspicious activity"):
            if marker in lowered:
                raise RunStopped(STATUS_BLOCKED, f'Instagram said "{marker}".')
        if "this account is private" in lowered:
            raise ReelFailed(STATUS_PRIVATE)
        if "page isn't available" in lowered or "page isn’t available" in lowered or "page not found" in lowered:
            raise ReelFailed(STATUS_NOT_AVAILABLE)

    # -- stats -------------------------------------------------------------

    def fetch_reel(self, shortcode: str, handle_hint: str) -> tuple[ReelStats, list[str]]:
        notes: list[str] = []
        self._json_bodies.clear()
        url = canonical_reel_url(shortcode)
        try:
            self.page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            raise ReelFailed(f"Failed to load: {type(exc).__name__}") from exc
        self.page.wait_for_timeout(4000)
        self._check_for_blockers()

        stats = ReelStats()

        # 1. Exact numbers from network responses and embedded JSON.
        for source_url, body in list(self._json_bodies):
            found = extract_stats_from_json(body, shortcode, source="network")
            if found:
                stats.merge_missing(found)
        if not stats.complete:
            for blob in self._embedded_json():
                found = extract_stats_from_json(blob, shortcode, source="page JSON")
                if found:
                    stats.merge_missing(found)
        if stats.raw_fields:
            log.debug("Raw fields for %s: %s", shortcode, stats.raw_fields)

        # 2. Visible text on the reel page.
        if not stats.complete:
            try:
                text = self.page.inner_text("body", timeout=5000)
            except Exception:
                text = ""
            found = extract_stats_from_text(text)
            # Likes must come from the post's own "N likes" link. Every comment
            # on the page also says "N likes", so a body-text match is unsafe.
            found.likes = None
            if stats.likes is None and not stats.likes_hidden:
                try:
                    link = self.page.locator('a[href*="/liked_by/"]')
                    if link.count() > 0:
                        m = re.search(r"([0-9][0-9,.]*\s*[KkMmBb]?)", link.first.inner_text(timeout=3000))
                        if m:
                            value, approx = parse_count(m.group(1))
                            if value is not None:
                                found.likes = value
                                found.approx = found.approx or approx
                except Exception:
                    pass
            before = (stats.views, stats.likes, stats.comments)
            stats.merge_missing(found)
            if (stats.views, stats.likes, stats.comments) != before:
                notes.append("some numbers read from page text")

        # 3. Views from the creator's Reels tab.
        if stats.views is None:
            handle = handle_hint or stats.owner_handle
            if handle:
                found = self._from_reels_tab(handle, shortcode)
                if found is not None and found.views is not None:
                    stats.merge_missing(found)
                    notes.append("views from Reels tab")
                else:
                    notes.append("views not found")
            else:
                notes.append("views not found (no handle)")
        # 4. Share count: not in the usual responses, so ask Instagram's own
        #    per-reel detail endpoint (read-only, same as the site does).
        if stats.shares is None and stats.media_id:
            found = self._media_info(stats.media_id, shortcode)
            if found:
                stats.merge_missing(found)
        if stats.raw_fields:
            log.debug("All raw fields for %s: %s", shortcode, stats.raw_fields)

        if stats.likes is None and stats.likes_hidden:
            notes.append("likes hidden")
        elif stats.likes is None:
            notes.append("likes not found")
        if stats.comments is None:
            notes.append("comments not found")

        if stats.views is None and stats.likes is None and stats.comments is None and not stats.likes_hidden:
            raise ReelFailed("Failed to read stats from the page")
        return stats, notes

    def _media_info(self, media_id: str, shortcode: str) -> Optional[ReelStats]:
        """Fetch /api/v1/media/<id>/info/ from inside the page (uses the login cookies)."""
        js = """
            async (id) => {
                try {
                    const r = await fetch(`https://www.instagram.com/api/v1/media/${id}/info/`, {
                        credentials: 'include',
                        headers: {'x-ig-app-id': '936619743392459', 'x-requested-with': 'XMLHttpRequest'}
                    });
                    if (!r.ok) return 'HTTP ' + r.status;
                    return await r.text();
                } catch (e) { return 'ERR ' + e; }
            }
        """
        try:
            body = self.page.evaluate(js, media_id)
        except Exception as exc:
            log.debug("media info request failed for %s: %s", shortcode, exc)
            return None
        if not isinstance(body, str) or body.startswith(("HTTP ", "ERR ")):
            log.debug("media info for %s unavailable: %s", shortcode, body)
            return None
        try:
            data = json.loads(body)
        except ValueError:
            return None
        found = extract_stats_from_json(data, shortcode, source="media info")
        if found:
            log.debug("media info fields for %s: %s", shortcode, found.raw_fields)
        return found

    def _from_reels_tab(self, handle: str, shortcode: str) -> Optional[ReelStats]:
        """Open the creator's Reels grid and return everything found for this reel."""
        url = f"https://www.instagram.com/{handle}/reels/"
        log.info("Views not on the reel page; checking %s", url)
        try:
            self.page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            log.warning("Reels tab failed to load: %s", exc)
            return None
        self.page.wait_for_timeout(4000)
        try:
            self._check_for_blockers()
        except ReelFailed:
            return None
        selector = f'a[href*="/reel/{shortcode}/"], a[href*="/p/{shortcode}/"]'
        for _ in range(12):  # scroll further down for older reels
            for source_url, body in list(self._json_bodies):
                found = extract_stats_from_json(body, shortcode, source="Reels tab")
                if found and found.views is not None:
                    return found
            tiles = self.page.locator(selector)
            if tiles.count() > 0:
                try:
                    text = tiles.first.inner_text(timeout=5000)
                except Exception:
                    text = ""
                for token in re.split(r"[\s\n]+", text):
                    value, approx = parse_count(token)
                    if value is not None:
                        return ReelStats(views=value, approx=approx, sources=["Reels tab"])
                return None
            self.page.mouse.wheel(0, 2500)
            self.page.wait_for_timeout(2000)
        return None


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"run_{datetime.now():%Y-%m-%d_%H%M%S}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    log.setLevel(logging.DEBUG)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    return log_file


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Daily Instagram reel stats tracker (read-only).")
    p.add_argument("--dry-run", action="store_true", help="Print results, write nothing to the sheet.")
    p.add_argument("--limit", type=int, default=None, help="Only process the first N rows with a link.")
    p.add_argument("--login", action="store_true",
                   help="Forget the saved Instagram session and wait for a fresh login (use this to switch accounts).")
    p.add_argument("--headless", action="store_true",
                   help="Run the browser invisibly (default is a visible window, which Instagram tolerates better).")
    p.add_argument("--sheet-id", default=DEFAULT_SHEET_ID, help="Google Sheet ID (from the URL).")
    p.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE, help="Service account JSON key.")
    p.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR, help="Browser profile folder.")
    p.add_argument("--min-delay", type=float, default=MIN_DELAY_SECONDS)
    p.add_argument("--max-delay", type=float, default=MAX_DELAY_SECONDS)
    p.add_argument("--verbose", action="store_true", help="Show debug details in the terminal.")
    return p.parse_args(argv)


def fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    log_file = setup_logging(args.verbose)
    log.info("Log file: %s", log_file)
    if args.dry_run:
        log.info("DRY RUN: nothing will be written to the sheet.")

    sheet = SheetClient(args.key_file, args.sheet_id, dry_run=args.dry_run)
    rows = sheet.load()
    if args.limit is not None:
        rows = rows[: args.limit]
    log.info("Found %d row(s) with a reel link to process.", len(rows))
    if not rows:
        return 0

    browser = InstagramBrowser(args.profile_dir, headless=args.headless and not args.login)
    updated: list[SheetRow] = []
    failed: list[tuple[SheetRow, str]] = []
    skipped: list[tuple[SheetRow, str]] = []
    history: list[list[Any]] = []
    results: list[tuple[SheetRow, Optional[ReelStats], str]] = []
    stop_status: Optional[str] = None
    stop_detail = ""
    seen: dict[str, int] = {}
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M")
    today = datetime.now().strftime("%Y-%m-%d")
    pending = list(rows)

    try:
        browser.start()
        browser.ensure_logged_in(force_login=args.login)

        while pending:
            row = pending.pop(0)
            # Duplicates and bad links never touch Instagram.
            if not row.shortcode:
                status = STATUS_INVALID_LINK
                skipped.append((row, status))
                results.append((row, None, status))
                sheet.write_row(row, {COL_STATUS: status})
                continue
            if row.shortcode in seen:
                status = f"Duplicate of row {seen[row.shortcode]}"
                skipped.append((row, status))
                results.append((row, None, status))
                sheet.write_row(row, {COL_STATUS: status})
                continue
            seen[row.shortcode] = row.row_number

            log.info("Row %s: %s", row.row_number, row.link)
            try:
                stats, notes = browser.fetch_reel(row.shortcode, row.handle)
            except ReelFailed as exc:
                status = exc.status
                log.warning("Row %s: %s (old numbers kept)", row.row_number, status)
                failed.append((row, status))
                results.append((row, None, status))
                sheet.write_row(row, {COL_STATUS: status})
            except RunStopped:
                pending.insert(0, row)
                raise
            else:
                status = build_status(stats, notes)
                values: dict[str, Any] = {COL_UPDATED: now_text, COL_STATUS: status}
                if stats.views is not None:
                    values[COL_VIEWS] = stats.views
                if stats.likes is not None:
                    values[COL_LIKES] = stats.likes
                elif stats.likes_hidden:
                    values[COL_LIKES] = "Hidden"
                if stats.comments is not None:
                    values[COL_COMMENTS] = stats.comments
                if stats.shares is not None and COL_SHARES in sheet.columns:
                    values[COL_SHARES] = stats.shares
                # Fill in the creator's name and handle only where the cell is empty.
                if not row.creator and stats.owner_name and COL_CREATOR in sheet.columns:
                    values[COL_CREATOR] = stats.owner_name
                if not row.handle and stats.owner_handle and COL_HANDLE in sheet.columns:
                    values[COL_HANDLE] = stats.owner_handle
                sheet.write_row(row, values)
                likes_cell = "Hidden" if (stats.likes is None and stats.likes_hidden) else stats.likes
                history.append([today, row.link,
                                stats.views if stats.views is not None else "",
                                likes_cell if likes_cell is not None else "",
                                stats.comments if stats.comments is not None else "",
                                stats.shares if stats.shares is not None else ""])
                updated.append(row)
                results.append((row, stats, status))
                log.info("Row %s: views=%s likes=%s comments=%s shares=%s (%s) via %s",
                         row.row_number, fmt(stats.views), fmt(likes_cell), fmt(stats.comments),
                         fmt(stats.shares), status, ", ".join(stats.sources) or "unknown")

            if pending:
                delay = random.uniform(args.min_delay, args.max_delay)
                log.info("Waiting %.1f seconds before the next reel...", delay)
                time.sleep(delay)

    except RunStopped as exc:
        stop_status, stop_detail = exc.status, exc.detail
        log.error("Run stopped: %s. %s", stop_status, stop_detail)
        for row in pending:
            failed.append((row, stop_status))
            results.append((row, None, stop_status))
            try:
                sheet.write_row(row, {COL_STATUS: stop_status})
            except Exception as write_exc:
                log.error("Could not write status for row %s: %s", row.row_number, write_exc)
    finally:
        browser.close()

    if history:
        try:
            sheet.append_history(history)
        except Exception as exc:
            log.error("Could not append to the History tab: %s", exc)

    # ---- summary -----------------------------------------------------------
    print("\n" + "=" * 78)
    print("RESULTS" + (" (dry run, nothing written)" if args.dry_run else ""))
    print("=" * 78)
    print(f"{'Row':<4} {'Handle':<18} {'Views':>12} {'Likes':>10} {'Comments':>9} {'Shares':>8}  Status")
    for row, stats, status in results:
        if stats is None:
            print(f"{row.row_number:<4} {row.handle[:18]:<18} {'':>12} {'':>10} {'':>9} {'':>8}  {status}")
            continue
        likes = "Hidden" if (stats.likes is None and stats.likes_hidden) else stats.likes
        print(f"{row.row_number:<4} {(row.handle or stats.owner_handle or '')[:18]:<18} "
              f"{fmt(stats.views):>12} {fmt(likes):>10} {fmt(stats.comments):>9} {fmt(stats.shares):>8}  {status}")
    print("-" * 78)
    print(f"Updated: {len(updated)}   Failed: {len(failed)}   Skipped: {len(skipped)}")
    if failed or skipped:
        print("Reasons:")
        for row, status in failed + skipped:
            print(f"  row {row.row_number}: {status}")
    if stop_status == STATUS_LOGIN_NEEDED:
        print("\nACTION NEEDED: Instagram wants you to log in again. Run:  python reel_stats.py --login")
    elif stop_status == STATUS_BLOCKED:
        print("\nACTION NEEDED: Instagram temporarily blocked automated viewing. "
              "Wait a few hours, open Instagram normally in the tracker's browser (python reel_stats.py --login), then try again.")
    print(f"Log file: {log_file}")
    log.info("Summary: updated=%d failed=%d skipped=%d stop=%s", len(updated), len(failed), len(skipped), stop_status)
    return 2 if stop_status else 0


if __name__ == "__main__":
    sys.exit(main())
