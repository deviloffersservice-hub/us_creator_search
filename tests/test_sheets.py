"""Sheets layer against an in-memory stand-in for gspread (no network)."""
import re

from gspread.utils import a1_to_rowcol

from creator_search import sheets


class FakeWS:
    def __init__(self, title, values=None):
        self.title, self.values = title, [list(r) for r in (values or [])]

    def row_values(self, n):
        return self.values[n - 1] if len(self.values) >= n else []

    def _put(self, row, col, val):
        while len(self.values) < row:
            self.values.append([])
        r = self.values[row - 1]
        while len(r) < col:
            r.append("")
        r[col - 1] = val

    def update(self, values, range_name, value_input_option=None):
        row, col = a1_to_rowcol(range_name)
        for i, vals in enumerate(values):
            for j, v in enumerate(vals):
                self._put(row + i, col + j, v)

    def freeze(self, rows):
        pass

    def get_all_values(self):
        return [[str(c) for c in r] for r in self.values]

    def append_rows(self, rows, **kw):
        self.values.extend([list(r) for r in rows])

    def batch_update(self, data, value_input_option=None):
        for d in data:
            self.update(d["values"], d["range"])


class FakeBook:
    def __init__(self, tabs):
        self.tabs = tabs

    def worksheets(self):
        return list(self.tabs.values())

    def values_batch_get(self, ranges, params=None):
        return {"valueRanges": [{"values": self.tabs[re.sub(r"^'|'$", "", r)].values} for r in ranges]}


def test_tab_creates_headers_and_round_trips():
    ws = FakeWS("Bulk")
    tab = sheets.Tab(ws, sheets.BULK_HEADERS)
    assert ws.values[0] == sheets.BULK_HEADERS
    tab.append([{"Name": "Jane", "Status": "Pending", "Followers": 12000}])
    rows = tab.rows()
    assert rows[0]["Name"] == "Jane" and rows[0]["_row"] == 2
    tab.set(2, {"Status": "Qualified", "Reason": "posted on 2026-10-20"})
    tab.flush()
    assert tab.rows()[0]["Status"] == "Qualified"


def test_tab_adds_missing_headers_without_touching_existing_columns():
    ws = FakeWS("Bulk", [["Name", "Custom column"], ["Jane", "keep me"]])
    tab = sheets.Tab(ws, ["Name", "Status"])
    assert tab.headers == ["Name", "Custom column", "Status"]
    assert tab.rows()[0]["Custom column"] == "keep me"


def test_known_creators_reads_links_hyperlinks_and_name_columns():
    book = FakeBook({
        "US Active Creators 5K-10K": FakeWS("US Active Creators 5K-10K", [
            ["100 US LinkedIn creators | 5K-10K"],
            ["", "#", "Creator", "Profile"],
            ["", "1", "Jane Doe", "https://www.linkedin.com/in/Jane-Doe/"],
            ["", "2", '=HYPERLINK("https://www.linkedin.com/in/john%2Dsmith","John Smith")', ""],
            ["", "3", "Name Only Person", "https://www.linkedin.com/posts/nameonly_hi-activity-7516240991830376448-XUsn"],
        ]),
    })
    s = sheets.Sheet.__new__(sheets.Sheet)
    s.book = book
    slugs, names, posts = s.known_creators()
    assert posts == ["https://www.linkedin.com/posts/nameonly_hi-activity-7516240991830376448-XUsn"]
    assert {"jane-doe", "john-smith"} <= slugs
    assert {"janedoe", "johnsmith", "nameonlyperson"} <= names


def test_time_format_round_trip():
    t = sheets.now_ist().replace(second=0, microsecond=0)
    assert sheets.parse_time(sheets.fmt_time(t)) == t
