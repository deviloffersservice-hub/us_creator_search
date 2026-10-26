"""v2 experiment: an improved pipeline that runs alongside v1 without touching it.

Writes only to its own spreadsheet (V2_SHEET_ID) and keeps its memory in GitHub's Actions
cache (.v2state/), so v1's sheet, tabs and crawl queue are never written or read as seeds.
It reuses v1's page parser, rules and niche list read-only.
"""
