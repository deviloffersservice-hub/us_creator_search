# v2 (the main pipeline)

v2 started as an experiment next to the original pipeline (v1), with its own spreadsheet. On
2026-10-26 it became the main pipeline:

| | v1 run 13 | v1 run 15 | v2 run 9 |
|---|---|---|---|
| Pages fetched | 1,100 | 1,100 | 600 |
| New creators | 6 | 40 | 48 |
| Qualified right away | 4 | 40 | 43 |
| Creators per 100 pages | 0.5 | 3.6 | 8.0 |

Yields swing a lot from run to run (it depends on which clusters of posts a run lands in), so
judge over a day of runs, not one.

It now writes to the main sheet (Bulk / Qualified / Run Log). The creators it found during the
experiment were copied over once (`v2.migrate`), and the experiment sheet is still read to avoid
duplicates. Memory lives in the GitHub Actions cache (`.v2state/`), and v1's Crawl Queue tab
isn't used.

## What v2 changes

- **Memory of people already checked.** People under 5K, over 1L, outside the US, or with no recent
  post are remembered for 7–120 days, depending on the reason. Their pages aren't fetched again.
- **Best-first crawl queue.** Up to 20,000 posts are kept in the cache, each tagged with its niche.
  v1's queue tab is ordered alphabetically, which loses the best leftovers.
- **Lanes that don't run dry.** Each niche has 5–8 search phrases, up to 3 tried per run. A lane
  with under 20 starting posts also gets topic-page posts up to a year old.
- **Better niche tags.** The tag comes from every post seen from that person, and a **Found Via**
  column says which niche lane found them.
- **Verify reuses page data from the last hour**, so the same page isn't downloaded twice. It still
  re-downloads for Watching rows.
- **Watching rows are checked faster**: every 2 hours for the first 48 hours, then every 4.
- **A chain that survives crashes.** Each run queues the next one at its *start*.
- **A Run Log tab** gets one row per run. The run **fails on purpose, so GitHub emails you**, if
  LinkedIn blocks it or 3 runs in a row find nobody.
- **Search tries ddgs "auto" first.** It answers far more often than any single engine. Each
  GitHub machine still gets only about 10–15 answered searches per run.
- **Related-post cards are used directly.** About 30% of cards show the author's follower count.
  People clearly under 4K or over 120K are rejected from the card, without fetching their post.
- **A per-run rejection breakdown in the logs.** It says why each fetched page's author didn't
  qualify (small / big / non-US / stale / already known), so changes can target the real loss.
- **Gentler on LinkedIn.** v2 stops after 3 blocked responses in a row (v1 waits for 5), and it
  gets a fresh GitHub machine (and IP) every hour.

## Controls

- **Start or restart:** go to **Actions → Creator pipeline v2 → Run workflow** and leave "chained" ticked.
- **Stop:** disable the "Creator pipeline v2" workflow in the Actions tab.
- **Per-run numbers:** see the Run Log tab, or the job log, which includes a rejection breakdown.

## Tried and dropped

- **Company pages** (`/company/<slug>/`) as a source of fresh posts. LinkedIn answers 999 on GitHub's
  machines from the first request. v2 tries once per run and switches them off on refusal.
