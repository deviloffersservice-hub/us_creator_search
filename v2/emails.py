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
import zlib
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


def _strip_tail(text: str, following: str) -> str:
    """Drop the next result's text when an engine glued it onto this one (Yahoo does this)."""
    following = following.strip()
    if len(following) >= 20:
        cut = text.find(following[:60])
        if cut >= 0:
            return text[:cut]
    return text


def own_texts(results: list[dict]) -> list[str]:
    """Each result's own title + snippet, with any text that bled in from later results removed."""
    out = []
    for i, r in enumerate(results):
        title, body = r.get("title", "") or "", r.get("body", "") or ""
        if i + 1 < len(results):
            title = _strip_tail(title, results[i + 1].get("title", "") or "")
            body = _strip_tail(body, results[i + 1].get("body", "") or "")
        out.append(f"{title} {body}")
    return out


def context(text: str, email: str, width: int = 140) -> str:
    """The words around the email, so a person can see what it was written next to."""
    flat = " ".join(text.split())
    at = flat.lower().find(email)
    if at < 0:
        return flat[:2 * width]
    return ("…" if at > width else "") + flat[max(0, at - width):at + len(email) + width]


def pick(results: list[dict], person: Person) -> Found | None:
    """The email shown on the person's own page, preferring one that looks like theirs.

    Rejects an address that also shows in another result (it is someone else's, or a shared
    inbox), and from a post accepts only an address that looks like theirs (job posts quote
    "send CVs to hr@..."). Profiles count either way.
    """
    texts = own_texts(results)
    elsewhere = {e for r, t in zip(results, texts) if not is_own_page(r.get("href", ""), person.slug)
                 for e in emails_in(t)}
    seen: list[tuple[str, str, str]] = []
    for r, text in zip(results, texts):
        if not is_own_page(r.get("href", ""), person.slug):
            continue
        on_post = "/posts/" in r["href"].lower()
        seen.extend((e, r["href"], text) for e in emails_in(text)
                    if e not in elsewhere and (not on_post or confidence(e, person) == "High"))
    if not seen:
        return None
    unique = list(dict.fromkeys(e for e, _, _ in seen))
    best = next((e for e in unique if confidence(e, person) == "High"), unique[0])
    href, text = next((h, t) for e, h, t in seen if e == best)
    return Found(best, tuple(e for e in unique if e != best), href, context(text, best))


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
    """Never searched, people whose last search was refused, and rows marked "Recheck" by hand."""
    return [p for p in everyone if rows.get(p.slug, {}).get("Status", "") in ("", "Error", "Recheck")]


def in_shard(slug: str, shard: int, shards: int) -> bool:
    """Fixed split by profile link, so shards never overlap even when they start at different times."""
    return zlib.crc32(slug.encode()) % shards == shard


def run(target: int, limit: int, max_minutes: float, dry_run: bool, searcher=search, pause=PAUSE_SECONDS,
        shard: int = 0, shards: int = 1) -> int:
    """Search the due people of this shard (a fixed split by profile link; each shard best-first).

    Shards only append and fill in rows; they never sort, so parallel shards can't move a row
    under each other. `finish` sorts once they are all done.
    """
    started = time.time()
    sheet = Sheet(config.V2_SHEET_ID)
    everyone = people(read_tab(sheet, config.CREATOR_SCORE_TAB))
    out = None if dry_run else sheet.tab(config.CREATOR_EMAIL_TAB, EMAIL_HEADERS)
    rows = existing_rows(out) if out else {}
    have = found_count(rows)
    todo = [p for p in due(everyone, rows) if in_shard(p.slug, shard, shards)]
    log.info("shard %d/%d: %d Strong/Possible creators; %d emails already found; %d to search here; target %d",
             shard + 1, shards, len(everyone), have, len(todo), target)
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
            have = _save(out, updates)  # recount: other shards add emails too
            updates = {}
        if refused_in_a_row >= GIVE_UP_AFTER:
            log.error("search engines refused %d people in a row; stopping for now", refused_in_a_row)
            break
    if out:
        have = _save(out, updates)
    log.info("done: searched %d; %d emails in the tab (target %d)", searched, have, target)
    return 2 if refused_in_a_row >= GIVE_UP_AFTER and searched == refused_in_a_row else 0


def finish(target: int) -> int:
    """After the shards: sort the tab (found first) and tell the workflow whether to go on."""
    tab = Sheet(config.V2_SHEET_ID).tab(config.CREATOR_EMAIL_TAB, EMAIL_HEADERS)
    sort(tab)
    rows = existing_rows(tab)
    everyone = people(read_tab(Sheet(config.V2_SHEET_ID), config.CREATOR_SCORE_TAB))
    have, todo = found_count(rows), len(due(everyone, rows))
    high = sum(1 for r in rows.values() if r.get("Confidence") == "High")
    left = 0 if have >= target else todo
    log.info("%d emails found (%d High, %d Check), target %d; %d people still to search",
             have, high, have - high, target, todo)
    _report(left)
    return 0


def _save(tab, updates: dict[str, dict]) -> int:
    """Write this batch and return how many emails the tab holds now."""
    if updates:
        write(tab, _with_new(tab, updates))
    return found_count(existing_rows(tab))


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
    ap.add_argument("--shard", default="1/1", help="k/n: search the k-th of n fixed parts of the list")
    ap.add_argument("--finish", action="store_true", help="no search: sort the tab, report what is left")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("primp", "ddgs", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if args.finish:
        sys.exit(finish(args.target))
    k, n = (int(x) for x in args.shard.split("/"))
    if not 1 <= k <= n:
        ap.error("--shard must be k/n with 1 <= k <= n")
    sys.exit(run(args.target, args.limit, args.minutes, args.dry_run, shard=k - 1, shards=n))


if __name__ == "__main__":
    main()
