"""Find the public contact email of the best creators in "India Creator Scores".

Many creators write an email for brand work in their LinkedIn headline or About section
("📩 collab: name@gmail.com"). Search engines show that text in their result snippets, so this
script searches for each creator and keeps an email only when it appears in the snippet of
that creator's own LinkedIn page (their /in/<slug> profile or one of their /posts/<slug>_...).
An email seen anywhere else (another person's page, a random site) is never used.

Order: Strong creators first, then Possible, best score first. It stops once the tab holds
CREATOR_EMAIL_TARGET found emails. People already searched are not searched again, except
those whose search failed (the search engines refused us), which are retried next run.

Results go to the "India Creator Emails" tab:
  Confidence "High"  - the email looks like theirs (has part of their name or a collab word)
  Confidence "Check" - found on their own page but doesn't obviously look like theirs
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import unquote

from creator_search.sheets import Sheet, fmt_time
from v2 import config
from v2.creators import existing_rows, read_tab, slug_of, sort, write

log = logging.getLogger("v2.emails")

EMAIL_HEADERS = [
    "Name", "Profile URL", "Tier", "Score", "Niche", "Followers", "Email", "Confidence",
    "Other Emails", "Found In", "Snippet", "Status", "Searched (IST)", "Rank",
]
STATUS_RANK = {("Found", "High"): 1, ("Found", "Check"): 2, ("Not found", ""): 3, ("Error", ""): 4}
TIERS = ("Strong", "Possible")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
PLACEHOLDER_DOMAINS = {"example.com", "domain.com", "email.com", "yourdomain.com", "company.com",
                       "mail.com", "xyz.com", "abc.com", "test.com", "linkedin.com", "sentry.io"}
PLACEHOLDER_LOCALS = {"your", "yourname", "name", "email", "abc", "xyz", "test", "example", "user",
                      "firstname", "username"}
FILE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
COLLAB_WORDS = ("collab", "brand", "business", "contact", "hello", "hi", "work", "connect",
                "partner", "pr", "media", "info", "team", "official", "mail")
BACKENDS = ("yahoo", "startpage", "auto")  # what ddgs offers for text search; auto = its own mix
QUERIES = (
    "{name} linkedin gmail",
    '"{name}" email site:linkedin.com/in',
    "{name} linkedin email collaboration",
)
PAUSE_SECONDS = 2.0
GIVE_UP_AFTER = 8  # people in a row whose every query was refused -> the engines are blocking us


@dataclass(frozen=True)
class Person:
    slug: str
    name: str
    url: str
    tier: str
    score: int
    niche: str
    followers: str


@dataclass(frozen=True)
class Found:
    email: str
    others: tuple[str, ...]
    source: str
    snippet: str


class SearchRefused(Exception):
    """Every search engine failed for this query (blocked or down), as opposed to no results."""


# --- pure helpers -----------------------------------------------------------------------------------

def clean_name(name: str) -> str:
    """'Deepa Chandrasekaran, Career Coach' -> 'Deepa Chandrasekaran'; drops titles like Dr."""
    first = re.split(r"[,|(\-–—]", name)[0]
    words = [w for w in re.sub(r"[^\w\s.]", " ", first).split() if w.lower().strip(".") not in ("dr", "ca", "cs")]
    return " ".join(words).strip(" .")


def emails_in(text: str) -> list[str]:
    """Real-looking emails in a snippet, in order, lowercased, without placeholders or file names."""
    out: list[str] = []
    for raw in EMAIL_RE.findall(text or ""):
        e = raw.strip(".").lower()
        local, _, domain = e.partition("@")
        if domain in PLACEHOLDER_DOMAINS or local in PLACEHOLDER_LOCALS or e.endswith(FILE_SUFFIXES):
            continue
        if e not in out:
            out.append(e)
    return out


def is_own_page(href: str, slug: str) -> bool:
    """True when a result is this creator's own profile or one of their own posts."""
    path = unquote(href or "").lower().split("?")[0].split("#")[0].rstrip("/") + "/"
    return f"/in/{slug}/" in path or f"/posts/{slug}_" in path


def confidence(email: str, person: Person) -> str:
    """High when the address carries part of their name/slug or a collab-ish word."""
    local = re.sub(r"[^a-z]", " ", email.split("@")[0].lower())
    tokens = {t for t in re.findall(r"[a-z]+", clean_name(person.name).lower() + " " + person.slug) if len(t) >= 4}
    squashed = local.replace(" ", "")
    if any(t in squashed for t in tokens):
        return "High"
    if any(w in local.split() or squashed.startswith(w) for w in COLLAB_WORDS):
        return "High"
    return "Check"


def pick(results: list[dict], person: Person) -> Found | None:
    """The email shown on the person's own page, preferring one that looks like theirs."""
    seen: list[tuple[str, str, str]] = []
    for r in results:
        if not is_own_page(r.get("href", ""), person.slug):
            continue
        text = f'{r.get("title", "")} {r.get("body", "")}'
        on_post = "/posts/" in r["href"].lower()
        # A post can quote someone else's address (job posts: "send CVs to hr@..."), so from a
        # post only an address that clearly looks like theirs counts. Profiles count either way.
        seen.extend((e, r["href"], text) for e in emails_in(text)
                    if not on_post or confidence(e, person) == "High")
    if not seen:
        return None
    unique = list(dict.fromkeys(e for e, _, _ in seen))
    best = next((e for e in unique if confidence(e, person) == "High"), unique[0])
    href, text = next((h, t) for e, h, t in seen if e == best)
    return Found(best, tuple(e for e in unique if e != best), href, " ".join(text.split())[:300])


def people(scores: list[dict]) -> list[Person]:
    """Strong then Possible, best score first, one per profile."""
    out: dict[str, Person] = {}
    for r in scores:
        tier = r.get("Tier", "")
        slug = slug_of(r.get("Profile URL", ""))
        if tier not in TIERS or not slug or slug in out:
            continue
        try:
            score = int(float(r.get("Score") or 0))
        except ValueError:
            score = 0
        out[slug] = Person(slug, r.get("Name", "").strip(), r.get("Profile URL", "").strip(), tier, score,
                           r.get("Niche", ""), r.get("Followers", ""))
    return sorted(out.values(), key=lambda p: (TIERS.index(p.tier), -p.score))


def row_for(person: Person, found: Found | None, status: str, now: datetime) -> dict:
    conf = confidence(found.email, person) if found else ""
    return {
        "Name": person.name, "Profile URL": person.url, "Tier": person.tier, "Score": person.score,
        "Niche": person.niche, "Followers": person.followers,
        "Email": found.email if found else "", "Confidence": conf,
        "Other Emails": ", ".join(found.others) if found else "",
        "Found In": found.source if found else "", "Snippet": found.snippet if found else "",
        "Status": status, "Searched (IST)": fmt_time(now), "Rank": STATUS_RANK[(status, conf)],
    }


def found_count(rows: dict[str, dict]) -> int:
    return sum(1 for r in rows.values() if r.get("Status") == "Found")


# --- searching ----------------------------------------------------------------------------------------

def search(query: str) -> list[dict]:
    """Results from the first engine that answers. No results anywhere -> []. All refused -> raise."""
    from ddgs import DDGS
    refused = 0
    for backend in BACKENDS:
        try:
            return DDGS().text(query, region="in-en", max_results=10, backend=backend)
        except Exception as e:  # ddgs raises its own types for blocks, timeouts and empty results
            if "no results" in str(e).lower():
                continue
            refused += 1
            log.debug("%s refused %r: %s", backend, query, str(e)[:100])
    if refused == len(BACKENDS):
        raise SearchRefused(query)
    return []


def find(person: Person, searcher=search, pause: float = PAUSE_SECONDS) -> tuple[Found | None, str]:
    """(email or None, status). Tries each query until one shows an email on their own page."""
    name, refused = clean_name(person.name) or person.slug, 0
    for q in QUERIES:
        try:
            hit = pick(searcher(q.format(name=name)), person)
        except SearchRefused:
            refused += 1
            hit = None
        if pause:
            time.sleep(pause)
        if hit:
            return hit, "Found"
    return None, "Error" if refused == len(QUERIES) else "Not found"


# --- run ----------------------------------------------------------------------------------------------

def due(everyone: list[Person], rows: dict[str, dict]) -> list[Person]:
    """Never searched, plus people whose last search was refused by the engines."""
    return [p for p in everyone if rows.get(p.slug, {}).get("Status", "") in ("", "Error")]


def run(target: int, limit: int, max_minutes: float, dry_run: bool, searcher=search, pause=PAUSE_SECONDS) -> int:
    started = time.time()
    sheet = Sheet(config.V2_SHEET_ID)
    everyone = people(read_tab(sheet, config.CREATOR_SCORE_TAB))
    out = None if dry_run else sheet.tab(config.CREATOR_EMAIL_TAB, EMAIL_HEADERS)
    rows = existing_rows(out) if out else {}
    have = found_count(rows)
    todo = due(everyone, rows)
    log.info("%d Strong/Possible creators; %d emails already found; %d still to search; target %d",
             len(everyone), have, len(todo), target)
    updates: dict[str, dict] = {}
    refused_in_a_row = searched = 0
    for p in todo:
        if have >= target or searched >= limit or time.time() - started > max_minutes * 60:
            break
        found, status = find(p, searcher, pause)
        searched += 1
        refused_in_a_row = refused_in_a_row + 1 if status == "Error" else 0
        have += status == "Found"
        updates[p.slug] = row_for(p, found, status, datetime.now(timezone.utc))
        log.info("[%d] %s (%s %d) -> %s %s", searched, p.name, p.tier, p.score, status,
                 f"{found.email} ({updates[p.slug]['Confidence']})" if found else "")
        if out and len(updates) >= config.CREATOR_FLUSH_EVERY:
            write(out, _with_new(out, updates))
            updates = {}
        if refused_in_a_row >= GIVE_UP_AFTER:
            log.error("search engines refused %d people in a row; stopping for now", refused_in_a_row)
            break
    if out:
        write(out, _with_new(out, updates))
        sort(out)
    left = 0 if have >= target else len(todo) - searched
    log.info("done: searched %d, %d emails in the tab (target %d), %d left to search", searched, have, target, left)
    _report(left if refused_in_a_row < GIVE_UP_AFTER else 0)
    return 2 if refused_in_a_row >= GIVE_UP_AFTER and searched == refused_in_a_row else 0


def _with_new(tab, updates: dict[str, dict]) -> dict[str, dict]:
    """Append rows for people not in the tab yet; return the rest for an in-place write."""
    known = existing_rows(tab)
    new = [u for s, u in updates.items() if s not in known]
    if new:
        tab.append(new)
    return {s: u for s, u in updates.items() if s in known}


def _report(left: int) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"left={left}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=int, default=config.CREATOR_EMAIL_TARGET, help="stop at this many emails")
    ap.add_argument("--limit", type=int, default=config.CREATOR_EMAIL_PER_RUN, help="people to search this run")
    ap.add_argument("--minutes", type=float, default=config.CREATOR_MAX_MINUTES)
    ap.add_argument("--dry-run", action="store_true", help="search and print, write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("primp", "ddgs", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    sys.exit(run(args.target, args.limit, args.minutes, args.dry_run))


if __name__ == "__main__":
    main()
