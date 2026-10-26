"""v2: the main pipeline since 2026-10-26 (it started as an experiment next to v1).

Writes to the sheet in V2_SHEET_ID (the main sheet) and keeps its memory in GitHub's Actions
cache (.v2state/); v1's Crawl Queue tab isn't used. It reuses v1's page parser, rules and niche
list unchanged.
"""
