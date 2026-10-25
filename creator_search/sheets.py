"""Google Sheets access via a service account (gspread)."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime

import gspread
from gspread.utils import absolute_range_name, rowcol_to_a1

from . import config
from .linkedin import POST_URL_RE, normalize_slug

log = logging.getLogger(__name__)

BULK_HEADERS = [
    "Added At (IST)", "Name", "Profile URL", "Niche", "Followers", "Total Posts",
    "Newest Post Seen", "Source Post URL", "Location Signal", "Status", "Reason",
    "Verified At (IST)",
]
QUALIFIED_HEADERS = [
    "Name", "Profile URL", "Niche", "Followers", "Total Posts", "Last Activity",
    "Latest Post URL", "Status", "First Qualified (IST)", "Last Checked (IST)", "Notes",
]

PROFILE_RE = re.compile(r"linkedin\.com/in/([^/\"'?#\s,)]+)", re.I)
QUEUE_HEADERS = ["Post URL", "Priority", "Niche"]
TIME_FMT = "%Y-%m-%d %H:%M"


def now_ist() -> datetime:
    return datetime.now(config.IST)


def fmt_time(dt: datetime | None) -> str:
    return dt.astimezone(config.IST).strftime(TIME_FMT) if dt else ""


def fmt_date(dt: datetime | None) -> str:
    return dt.astimezone(config.IST).strftime("%Y-%m-%d") if dt else ""


def parse_time(text: str) -> datetime | None:
    """Parses what fmt_time / fmt_date wrote (interpreted as IST)."""
    text = (text or "").strip()
    for fmt in (TIME_FMT, "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=config.IST)
        except ValueError:
            pass
    return None


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _client() -> gspread.Client:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        return gspread.service_account_from_dict(json.loads(raw))
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")
    return gspread.service_account(filename=path)


class Tab:
    """A worksheet addressed by header names. Writes are RAW so values never get reformatted."""

    def __init__(self, ws: gspread.Worksheet, headers: list[str]):
        self.ws = ws
        current = ws.row_values(1)
        if not any(current):
            ws.update(values=[headers], range_name="A1", value_input_option="RAW")
            ws.freeze(rows=1)
            current = list(headers)
        missing = [h for h in headers if h not in current]
        if missing:
            current = current + missing
            col_count = getattr(ws, "col_count", len(current))
            if col_count < len(current):
                ws.add_cols(len(current) - col_count)
            ws.update(values=[current], range_name="A1", value_input_option="RAW")
        self.headers = current
        self._pending: list[dict] = []

    def rows(self) -> list[dict]:
        values = self.ws.get_all_values()
        out = []
        for i, row in enumerate(values[1:], start=2):
            if not any(row):
                continue
            rec = {h: (row[j] if j < len(row) else "") for j, h in enumerate(self.headers)}
            rec["_row"] = i
            out.append(rec)
        return out

    def append(self, records: list[dict]):
        if not records:
            return
        rows = [[r.get(h, "") for h in self.headers] for r in records]
        self.ws.append_rows(rows, value_input_option="RAW", insert_data_option="INSERT_ROWS",
                            table_range="A1")

    def set(self, row: int, values: dict):
        for key, val in values.items():
            col = self.headers.index(key) + 1
            self._pending.append({"range": rowcol_to_a1(row, col), "values": [[val]]})

    def flush(self):
        # Chunked so a long run never sends one giant request.
        while self._pending:
            chunk, self._pending = self._pending[:500], self._pending[500:]
            self.ws.batch_update(chunk, value_input_option="RAW")


class Sheet:
    def __init__(self, sheet_id: str = config.SHEET_ID):
        self.book = _client().open_by_key(sheet_id)

    def tab(self, title: str, headers: list[str]) -> Tab:
        try:
            ws = self.book.worksheet(title)
        except gspread.WorksheetNotFound:
            log.info("creating tab %r", title)
            ws = self.book.add_worksheet(title=title, rows=1000, cols=len(headers))
        return Tab(ws, headers)

    def bulk(self) -> Tab:
        return self.tab(config.BULK_TAB, BULK_HEADERS)

    def qualified(self) -> Tab:
        return self.tab(config.QUALIFIED_TAB, QUALIFIED_HEADERS)

    def queue(self) -> Tab:
        return self.tab(config.QUEUE_TAB, QUEUE_HEADERS)

    def replace_queue(self, entries: list[tuple]):
        tab = self.queue()
        tab.ws.batch_clear(["A2:C"])
        tab.ws.resize(rows=max(len(entries) + 1, 2))
        if entries:
            tab.ws.update(values=[list(e) for e in entries], range_name="A2", value_input_option="RAW")

    def known_creators(self) -> tuple[set[str], set[str], list[str]]:
        """Every LinkedIn profile slug, creator name and post URL already anywhere in the spreadsheet.

        Reads formulas too, so =HYPERLINK("https://linkedin.com/in/x", "Name") cells count.
        Names come from columns headed like "Name"/"Creator" in any tab (incl. the GPT tabs).
        Post URLs (from every tab except the crawl queue) are used as crawl seeds.
        """
        titles = [ws.title for ws in self.book.worksheets()]
        resp = self.book.values_batch_get([absolute_range_name(t) for t in titles],
                                          params={"valueRenderOption": "FORMULA"})
        slugs, names, posts = set(), set(), []
        for title, vr in zip(titles, resp.get("valueRanges", [])):
            rows = vr.get("values", [])
            name_col, header_row = None, None
            for i, row in enumerate(rows[:15]):
                for j, cell in enumerate(row):
                    if re.fullmatch(r"\s*(creator\s*)?(full\s*)?name\s*|\s*creator\s*", str(cell), re.I):
                        name_col, header_row = j, i
                        break
                if name_col is not None:
                    break
            for i, row in enumerate(rows):
                for cell in row:
                    for m in PROFILE_RE.finditer(str(cell)):
                        slugs.add(normalize_slug(m.group(1)))
                    if title != config.QUEUE_TAB:
                        posts += POST_URL_RE.findall(str(cell))
                if name_col is not None and i > header_row and name_col < len(row):
                    cell = str(row[name_col])
                    hyper = re.search(r'HYPERLINK\([^,]+,\s*"([^"]+)"', cell, re.I)
                    n = normalize_name(hyper.group(1) if hyper else cell)
                    if n:
                        names.add(n)
        log.info("already in sheet: %d profile links, %d names, %d post links", len(slugs), len(names), len(posts))
        return slugs, names, list(dict.fromkeys(posts))
