# v2 experiment

v2 is an improved version of the pipeline. It runs **alongside** the main one (v1) without changing it:

| | v1 (main) | v2 (experiment) |
|---|---|---|
| Code | `bulk_add.py`, `verify.py`, `recheck.py` | `v2/` (reuses v1's parser, rules and recheck logic read-only) |
| Workflow | `creator-pipeline.yml`, about :23 past each hour | `experiment-v2.yml`, about :53 past each hour |
| Output | Main sheet (Bulk / Qualified / Crawl Queue) | [US Creators – v2 experiment](https://docs.google.com/spreadsheets/d/1zHA9Rm3tniFflGhO8w-1ZGKsXaFBiFVktasFvI-TYBk/edit) (Bulk / Qualified / Run Log) |
| Memory | Crawl Queue tab | GitHub Actions cache (`.v2state/`) |
| Pages per run | 1,100 | 600 |

v2 reads the main sheet only to avoid adding people who are already there. It never writes to it.

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
  works on different niches than v1 in the same hour.

## Controls

- **Start or restart:** go to **Actions → Experiment v2 → Run workflow** and leave "chained" ticked.
- **Stop:** disable the "Experiment v2" workflow in the Actions tab. v1 keeps running.
- **Compare:** v2's Run Log tab shows per-run numbers. For v1, read its job logs.

## Tried and dropped

- **Company pages** (`/company/<slug>/`) as a source of fresh posts. LinkedIn answers 999 on GitHub's
  machines from the first request. v2 tries once per run and switches them off on refusal.
