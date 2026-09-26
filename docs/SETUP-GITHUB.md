# First-time GitHub setup (click by click)

This is done **once**. It takes about 15 minutes, plus a few hours of waiting while the first
big update runs by itself. You need to be an **owner/admin** of the repository.

Repository: **https://github.com/MKP715/AAGrapevine** — if yours has another name, use yours
everywhere below.

---

## Step 1 — Put the code on GitHub

*Skip this step if the repository on GitHub already contains the folders `.github`, `config`,
`scripts` and `src`.*

**Easiest (GitHub Desktop, no typing):**

1. Install **GitHub Desktop** from <https://desktop.github.com/> and sign in.
2. **File → Add local repository…** → choose the project folder (the one containing `README.md`).
3. In the left panel, type a summary such as `New self-updating website` → **Commit to main**.
4. Click **Push origin** (top bar).

**Or with the command line** (in the project folder):

```bash
git add -A
git commit -m "New self-updating website"
git push origin main
```

*Pushing starts an **Update & Deploy** run by itself. Until Step 3 is done it may end with a
red ✗ at "Read GitHub Pages settings" — that is expected; Step 6 runs it again properly.*

---

## Step 2 — Keep the repository public

**Settings** (top of the repository page) → **General** → scroll to **Danger Zone** → the
visibility should say **Public**.

Why: GitHub Actions is **free and unlimited** for public repositories. (A private repository gets
2,000 free minutes a month — the daily update would use almost all of them.) The website is public
anyway, and no secrets are stored in the files.

---

## Step 3 — Let GitHub Actions publish the website

1. **Settings** → **Pages** (left menu, under *Code and automation*).
2. Under **Build and deployment** → **Source**, choose **GitHub Actions**.
   (Not "Deploy from a branch".)
3. Nothing else to fill in on this page for now.

---

## Step 4 — Let GitHub Actions run

1. **Settings** → **Actions** → **General**.
2. **Actions permissions:** choose **Allow all actions and reusable workflows** (usually already
   selected). *(If your organization restricts this, allow at least GitHub's own actions and
   `lycheeverse/lychee-action`.)*
3. **Workflow permissions:** leave it as it is (the default, read-only, is best). Each workflow
   asks for exactly the access it needs — for example, the daily update asks for permission to
   save its data — so nothing has to be widened here.

> **Branch protection:** if you ever add a rule protecting `main`
> (**Settings → Rules** or **Settings → Branches**), add **GitHub Actions** to its bypass list;
> otherwise the daily data commit is refused (the run then fails at "Commit refreshed data").

---

## Step 5 — Check the settings file

1. Open **`config/site.yml`** in the repository → pencil icon (**Edit this file**).
2. Check `url:` under `site:` is the address the site will have:
   `https://<account>.github.io/<repository>` — for example `https://mkp715.github.io/AAGrapevine`
   (no slash at the end). Pages shows the exact address after the first deploy (Step 7).
3. Check the committee meeting, Zoom details and `contact_email`.
4. Check `drive:` → `root_folder_id` is the committee's shared folder, and that the folder is shared
   as **Anyone with the link — Viewer** (in Google Drive: right-click the folder → **Share** →
   *General access*).
5. **Commit changes…** → **Commit changes**.

*(Committing this file starts a quick update automatically. That's fine — let it run, or cancel it
in the Actions tab and continue with Step 6. A cancelled run still saves what it fetched.)*

---

## Step 6 — Run the first update

1. Click the **Actions** tab.
   - If GitHub shows *"Workflows aren't being run on this repository"*, click
     **I understand my workflows, go ahead and enable them**.
2. In the left list click **Update & Deploy**.
3. Click **Run workflow** (right side). Fill in:
   - **Use workflow from:** `main`
   - **crawl_minutes:** leave it **empty** (= the daily setting, 40 minutes). The big first search
     of aagrapevine.org and aalavina.org was run ahead of time and its progress saved in the
     repository (`data/state/crawl-state.json`), so the daily runs simply continue from there.
     Use **`300`** (about 5 hours) only if that first search was **not** saved — see Step 7, point 3.
   - **skip_crawl:** leave unticked
4. Click the green **Run workflow** button.

You can close the browser. What happens:

| Part | Time | What it does |
|---|---|---|
| *Sync content + translate* | ~1–1½ hours (up to ~6 hours with `300`) | Fetches every source, downloads the free translation models (~175 MB, only the first time), translates new titles, commits the data |
| *Build & publish website* | ~3 minutes | Builds the site and publishes it on GitHub Pages |

*In a hurry?* Run it first with **skip_crawl** ticked (about 15 minutes) to get the site online; it
refreshes only Google Drive, announcements, the podcasts and the daily quote, and the next daily run does the rest.

---

## Step 7 — Check the result

1. When the run shows a green ✓, open it: under **Build & publish website** GitHub shows the
   website address. Also visible in **Settings → Pages** ("Your site is live at …").
2. Open the site. Check the English and Spanish versions (`…/es/`).
3. Open **`…/status/`** on the site: every source should show a recent date. The **PDF search**
   box shows how much of the two magazine sites has been searched. If it shows only a small
   percentage (the first big search was not saved), you may run **Update & Deploy** once with
   `crawl_minutes` = `300` — or simply wait: the daily 40-minute search gets there in about 10 days.
4. Optional but handy: on the repository's main page, click the ⚙ gear next to **About** →
   tick **Use your GitHub Pages website** → **Save changes**. The address now shows at the top
   of the repository.

From now on the site updates itself **every morning at about 5:17 AM Central**.

**What to expect on day 1:** magazine stories, both podcasts (AA Grapevine's Podcast and the
Grapevine Weekly Open AA Meeting), videos and Instagram appear right away. The committee's own
sections (Portfolio, Photos, flyer events, Drive announcements) show a friendly "nothing here yet"
message until files are uploaded to the Panel 77 folders in Google Drive — that is expected.
Translations of a large first batch may take a few daily runs; untranslated titles show in their
original language meanwhile.

---

## Step 8 — Get told when something breaks

1. Click your profile picture (top right of GitHub) → **Settings** → **Notifications**.
2. Under **System → Actions**, tick **Email** and **Only notify for failed workflows**.
3. **Make the daily run's e-mails come to you.** GitHub sends them to the person who last switched
   the workflow on (or last changed its schedule) — not automatically to whoever ticked step 2.
   Open the repository's **Actions** tab → **Update & Deploy** → **⋯** (top right) →
   **Disable workflow**, then the same menu (or the banner) → **Enable workflow**.
4. On the repository page, check the **Watch** button (top right) is set to **All Activity**, or
   **Custom** with **Issues** ticked, so you receive the issue described below. (People who can
   push to the repository usually watch it already.)

GitHub then e-mails you if a scheduled update fails. One source having a bad day is *not* a
failure — the site simply keeps that source's previous items. If the **same** source has not
updated for **7 days**, the update opens one issue, **"A content source has stopped updating"**,
that says what to check; it closes itself when the source works again.

---

## Optional next steps

| What | Where |
|---|---|
| Monthly bilingual e-mail to the districts | README → *Optional upgrades → Monthly e-mail digest* |
| Exact dates for Drive files (Google API key) | README → *Optional upgrades → Google API key* |
| Instagram the official way (token), or no automated Instagram visits at all | README → *Instagram: how the site reads it* |
| Your own address, e.g. grapevine.neta65.org | README → *Using your own address* |
| Send visitors of the old site to the new one | README → *Replacing the old site* |
| Weekly broken-link report | Nothing to do — the **Weekly link check** workflow runs on Sundays and opens an issue only if it finds a problem |
| Safe monthly updates (Dependabot) | Nothing to do — the **Code check** workflow builds the site and runs the tests for every pull request; merge one only when it shows a green ✓ |

### Where to add secrets

**Settings → Secrets and variables → Actions → New repository secret.** The name must match
exactly (capital letters, underscores). Secrets can be replaced any time but never viewed again.

| Secret name | Needed for | Required? |
|---|---|---|
| `GOOGLE_API_KEY` | Exact Drive dates | No |
| `IG_ACCESS_TOKEN`, `IG_BUSINESS_ID` | Official Instagram API | No |
| `SMTP_SERVER`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `DIGEST_TO` | Monthly e-mail | Only for the e-mail |
| `SMTP_PORT`, `DIGEST_FROM`, `DIGEST_REPLY_TO` | Monthly e-mail fine-tuning | No |

---

## If you move or copy the repository later

After a **transfer**, **rename** or **fork**, repeat Steps 3, 4 and 8, re-add any secrets that did
not come along, update `url:` in `config/site.yml`, and run **Update & Deploy** once. The workflow
works out the new address and folder name by itself — no code changes are needed.
A fork also needs **Actions → Enable workflows**; scheduled runs only happen on the `main` branch.

**New owner or new chair:** the failure e-mails of the daily run keep going to the person who last
switched the workflow on. Whoever should receive them now does Step 8, point 3
(**Actions → Update & Deploy → ⋯ → Disable workflow**, then **Enable workflow**).
