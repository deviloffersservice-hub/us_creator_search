# US LinkedIn creator search

Finds US-based LinkedIn creators with **5,000–100,000 followers** who have **posted in the last
14 days**, in any niche. Results go into the Google Sheet
[US LinkedIn Tech Creators](https://docs.google.com/spreadsheets/d/1GdZxogrK0wYMXcWGC3tbelUrEZE39foweCHNBbK-6-w/edit).
It's free: no LinkedIn login, no paid APIs, and it runs on GitHub Actions.

| Script | When (IST) | What it does |
|---|---|---|
| `bulk_add.py` | 08:00, 13:00, 18:00 | Adds 100 new creators to the **Bulk** tab as `Pending` |
| `verify.py` | right after each bulk run | Re-checks every `Pending`/`Watching` row → `Qualified` / `Rejected` / `Watching`; copies qualified ones to **Qualified** |
| `recheck.py` | 06:00 daily | Re-checks everyone in **Qualified** → `Active` / `Inactive` / `Out of range` |

No duplicates: before adding anyone, the script reads **every tab** in the spreadsheet,
including the GPT-made tabs. It skips anyone whose LinkedIn profile link is already
anywhere in the sheet, or whose name already appears in a Name/Creator column.

## How it works without logging in

LinkedIn blocks logged-out visitors from profile pages, but it serves **public post pages**
(`linkedin.com/posts/...`) to anyone. Each post page contains:

- **Exact follower count** of the author (from LinkedIn's own page data)
- **Exact post time**: the number in every post URL is a timestamp
- **Total number of posts** the author has made
- **~10 "More Relevant Posts"** by other people. Each one links to its author's profile, and that
  link's subdomain gives their country: `www.linkedin.com/in/...` = **United States**, while
  `uk.`, `in.`, `ca.` and so on are other countries.

`bulk_add.py` crawls from post to post, newest posts first, and follows US people it hasn't seen.
It saves its position in the **Crawl Queue** tab so the next run carries on from there.

**The activity check** (`verify.py`): a creator qualifies right away if we can see a post from the
last 14 days. If the newest post we can see is older, the creator stays `Watching`. Their total
post count is checked again on later runs (at most every 4 hours), and if it has gone up,
they've posted since the last check, so they're `Qualified`. After 14 days with no new post
they're `Rejected` as inactive. `recheck.py` uses the same post-count signal to keep the
Qualified tab up to date.

## Limits to know about

- **Location** is LinkedIn's own country assignment, shown by the profile link's domain
  (www = US). It's the only location signal available without logging in, and city/state
  aren't visible. Watch for rare edge cases, like someone whose LinkedIn region is still the US
  after moving abroad.
- **Post count goes up** for new posts and probably reposts too, so a repost may count as
  activity.
- If LinkedIn starts blocking GitHub's servers, the scripts stop politely and leave rows
  `Pending` for the next run. The run log in the Actions tab will say so.
- The free DuckDuckGo search that adds extra seed posts is often rate-limited. The crawler
  doesn't depend on it.

## Setup (one time, about 10 minutes)

### 1. Create a Google service account (free)
The scripts need their own Google login to write to the sheet.

1. Go to <https://console.cloud.google.com/>, then **Select a project → New project** (any name).
2. Go to **APIs & Services → Library**, search for **Google Sheets API**, and click **Enable**.
3. Go to **IAM & Admin → Service Accounts → Create service account**. Give it any name and skip the
   optional role steps.
4. Open the new service account, then go to **Keys → Add key → Create new key → JSON**. A `.json`
   file downloads.
   - If Google says *"Service account key creation is disabled"*, that's an organisation policy on
     the anchors.in Google Workspace. Either ask the Workspace admin to allow it for this project,
     or create the project with a personal Gmail account instead.
5. Copy the `client_email` from the JSON file (it looks like `name@project.iam.gserviceaccount.com`).

### 2. Share the sheet with it
Open the spreadsheet, click **Share**, paste that `client_email`, and set it to **Editor**.

### 3. Add the key to GitHub
In this repo, go to **Settings → Secrets and variables → Actions → New repository secret**:
- Name: `GOOGLE_SERVICE_ACCOUNT_JSON`
- Value: the **entire contents** of the downloaded JSON file

Then delete the JSON file from your computer, or keep it somewhere safe. Never commit it.

### 4. Turn it on
GitHub only runs scheduled workflows from the repository's **default branch**. Right now that's
`claude/gallant-edison-hjrvij`, because it was the first branch pushed to this empty repo. If you
later create `main`, merge this code into it and make `main` the default. To test right away, go to
**Actions → Creator pipeline (bulk add + verify) → Run workflow**. Tick **Dry run** to just crawl
and print results without touching the sheet.

## Running locally

```bash
pip install -r requirements.txt
export GOOGLE_SERVICE_ACCOUNT_JSON="$(cat service_account.json)"
python bulk_add.py --target 20      # add 20 creators
python verify.py                    # verify Pending/Watching rows
python recheck.py --limit 50        # recheck 50 qualified creators
python bulk_add.py --no-sheet --target 10   # dry run: print, don't touch the sheet
python -m pytest                    # offline tests
```

All thresholds (followers range, 14 days, 100 per run, delays, and so on) are in
`creator_search/config.py`, and each can be overridden with an environment variable of the same name.

## What to expect

Measured on a test run: one bulk run opens about 375–500 post pages and finds 100 new
US creators with 5K–100K followers in about 10 minutes (median about 10K followers). On that run:

- About 10–20% had a post from the last 14 days, so they're `Qualified` straight away.
- Most others had a post visible from the last 2–6 weeks. They start as `Watching`, and the
  prolific ones (many have 1,000+ posts in total) move to `Qualified` on a later run once
  their post count goes up.
- Anyone with no new post after 14 days of checks is `Rejected` with the reason given.

Discovery follows LinkedIn's "related posts", so a run can lean towards one topic cluster.
A soft cap of 25 per niche per run (`NICHE_CAP_PER_RUN`) pushes the crawl to spread out. You can
add a public post URL from a niche you want more of to `seeds.txt`.

## GitHub Actions minutes

The repo is private, and the GitHub Free plan includes **2,000 Actions minutes/month** for private repos.
One bulk+verify run takes about 15 minutes and the recheck about 10, which comes to
**roughly 1,500–1,800 minutes a month**. That's inside the free allowance, but close to it.
If you run out, either make the repo public (public repos get unlimited minutes, and no secrets
are stored in the code) or lower `BULK_TARGET` / `MAX_PAGES_PER_BULK_RUN`.

If the **Actions** tab shows nothing after you merge, check **Settings → Actions → General**
and make sure Actions are allowed for this repository.
