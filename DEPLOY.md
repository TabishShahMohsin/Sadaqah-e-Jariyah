# Putting Sadaqah-e-Jariyah online

About an hour, once. You need a laptop for steps 1–6; after that everything runs from GitHub and your phone.

```
visitor ──► Contribute page ──► intake Worker ──► sej-inbox (private R2)
                                     │                 │
                                     ▼                 ▼
                              GitHub issue ──► "Sort contribution" Action (Claude)
                                                       │
                                                       ▼
                                          Pull request ── you approve (merge)
                                                       │
                                                       ▼
                     "Publish" Action ──► sej-files (public R2) + rebuilt site on Cloudflare Pages
```

## 0. Stay anonymous (do this first)
The project carries no maintainer's name anywhere. Things that would otherwise leak it:

| Leak | Fix |
|---|---|
| **Git history.** Existing commits carry your name and email, and `origin` points at your personal GitHub. | Make a new GitHub account just for the project (new email, neutral name such as `sadaqah-e-jariyah`). Start a fresh history: rename `.git` to `.git-old` (keep it privately or delete it), `git init`, then set the identity **for this repo only**: `git config user.name "Sadaqah-e-Jariyah"` and `git config user.email "<id>+<user>@users.noreply.github.com"`. Push to the new account. |
| **GitHub activity.** Merging PRs or commenting from your personal account shows your username. | Do all project work while signed into the project account. |
| **Cloudflare / workers.dev.** The Worker URL contains your account subdomain. | Set the workers.dev subdomain to something neutral, or (better) give the Worker a route on your domain, e.g. `api.sejariyah.org`. Name the Pages project `sej`. |
| **Domain WHOIS.** | Cloudflare Registrar redacts owner details by default; other registrars need privacy turned on. |
| **Hidden data in files.** Photos can carry GPS location, owner name and camera serial; PDFs and Office files carry author names. | `python3 tools/scrub.py --check --all-types` lists them. `--fix` cleans images (done once already); `--fix --all-types` also blanks PDF and Office author fields. Originals are copied to `_Inbox/_Originals-before-anonymising/`. Contributions are scrubbed automatically. |
| **Your own work in the archive** (names in lab programs, handwritten notes with your name on the page). | Searched text files: 3 had your name; they're cleaned. Handwritten scans can't be searched; skim the first page of your own notes. |
| **API keys and billing** (Anthropic, Google Cloud, Cloudflare). | Private to the account holder and never shown publicly; using a project email still keeps things separate. |

After cleaning, run `python3 tools/sej.py import --force` so the catalog matches the cleaned files.

## 0b. Before anything is public
Books-and-Resources folders contain full textbooks (Silberschatz, Bishop, Pressman, Schaum's, …). Move them to
`_Inbox/_Not-Public/` (that folder is never published), then run `python3 tools/sej.py scan --remove` so the
catalog forgets them.

## 1. Cloudflare: storage
1. Create a free Cloudflare account. Add a payment method (R2 requires one; ~20 GB costs about $0.15/month).
2. **R2 → Create bucket** `sej-files`. Settings → **Public access**: connect your custom domain
   (e.g. `files.sejariyah.org`) or enable the `r2.dev` URL. Settings → **CORS policy**:
   ```json
   [{"AllowedOrigins": ["https://sej.pages.dev", "https://sejariyah.org"], "AllowedMethods": ["GET", "HEAD"], "AllowedHeaders": ["*"]}]
   ```
3. **Create bucket** `sej-inbox`. **Never** enable public access. Settings → **Object lifecycle rules** →
   delete objects with prefix `inbox/` after 30 days.
4. **R2 → Manage API tokens → Create token**: *Object Read & Write*, both buckets. Note the Access Key ID,
   Secret, and the endpoint `https://<account-id>.r2.cloudflarestorage.com`.

## 2. Upload the archive (from the laptop)
```bash
brew install rclone
rclone config        # new remote "r2", type s3, provider Cloudflare, paste the key, secret and endpoint
cd ~/Desktop/Sadaqah-e-Jariyah
python3 tools/sej.py paths > /tmp/sej-paths.txt
rclone copy . r2:sej-files --files-from /tmp/sej-paths.txt --progress --transfers 8
```

## 3. Human check (Turnstile)
Cloudflare → **Turnstile → Add widget**. Hostnames: `sej.pages.dev` and your domain. Mode: *Managed*.
Note the **site key** and **secret key**.

## 4. The intake Worker
```bash
cd worker
npm install
npx wrangler login
npx wrangler d1 create sej-intake        # copy the database_id into wrangler.toml
npm run db:init
openssl rand -hex 32                     # this is your ADMIN_TOKEN; keep it for step 5 too
npx wrangler secret put ADMIN_TOKEN
npx wrangler secret put TURNSTILE_SECRET
npx wrangler secret put GITHUB_TOKEN     # see 5.2
# edit ALLOWED_ORIGINS in wrangler.toml to your site URL(s)
npx wrangler deploy                      # prints https://sej-intake.<you>.workers.dev
npm test                                 # optional: runs the 33 local checks
```

## 5. GitHub
1. Move the two workflows into place (they were written to `setup/` because `.github/` is protected from remote edits):
   `mkdir -p .github/workflows && mv setup/github-workflows/*.yml .github/workflows/`
   Push the repo (public, so anyone can see the catalog and send fixes). Create a label named `submission`.
2. **Token for the Worker**: Settings → Developer settings → Fine-grained token → only this repository →
   *Issues: Read and write*. Nothing else. Put it in the Worker as `GITHUB_TOKEN` (step 4).
3. Repo → Settings → Actions → General → enable **“Allow GitHub Actions to create and approve pull requests”**.
4. Repo → Settings → Secrets and variables → Actions.

   | Secrets | |
   |---|---|
   | `ANTHROPIC_API_KEY` | console.anthropic.com → API keys (a few cents per contribution) |
   | `ADMIN_TOKEN` | same value as the Worker's |
   | `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | from 1.4 |
   | `DRIVE_API_KEY` | Google Cloud console → enable *Google Drive API* → Credentials → API key, restricted to Drive API |
   | `CLOUDFLARE_API_TOKEN` | Cloudflare → My Profile → API Tokens → *Cloudflare Pages: Edit* |
   | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare dashboard sidebar |

   | Variables | Example |
   |---|---|
   | `INTAKE_API` | `https://sej-intake.<you>.workers.dev` |
   | `FILES_BASE_URL` | `https://files.sejariyah.org/` (trailing slash) |
   | `TURNSTILE_SITEKEY` | from step 3 |
   | `SITE_URL` | `https://sej.pages.dev` |
   | `SEJ_MODEL` | optional; defaults to `claude-sonnet-5-5` |
   | `PREVIEW_LINKS` | `true` puts 3-day file links in PRs so you can review on your phone; `false` to hide them |

## 6. The site
```bash
npx wrangler pages project create sej --production-branch main
```
Then GitHub → Actions → **Publish** → *Run workflow*. Your site is at `https://sej.pages.dev`.
Custom domain: Pages project → Custom domains.

## Daily use (phone is enough)
- A contribution arrives → an issue opens → a few minutes later a **pull request** lists each file, where it goes,
  how sure Claude is, and anything skipped (textbooks, duplicates, fake files) with the reason.
- Open the file links, check the destinations, **merge**. The Publish action copies the files, rebuilds the
  site, and tells the contributor. To change a destination, edit the JSON file in the PR before merging.
- Something went wrong? Actions → **Sort contribution** → *Run workflow* with the issue number (tick *force* to re-sort).

## Adding files yourself
```bash
# put files in the right folders on the laptop, then:
python3 tools/sej.py scan                 # records them in catalog/changes/
python3 tools/sej.py paths --local-only > /tmp/p.txt && rclone copy . r2:sej-files --files-from /tmp/p.txt
git add catalog && git commit -m "Add files" && git push
```
`python3 serve.py` still browses your local folder offline.

## Why it's safe
- Uploads land in a private bucket; nothing is public until you merge.
- The Worker checks the human test, rate-limits per visitor and globally, allows only study formats up to
  500 MB, checks file contents match their type, and gives each upload its own secret token.
- Issues carry a signature only the Worker can make; forged issues are ignored. Issue text never reaches a shell.
- Claude only proposes. `tools/rules.py` rejects any folder or name that breaks CONVENTIONS.md, and
  publishing re-checks every entry before copying.
- Files are served from a separate domain, and HTML/SVG are never accepted, so a file can't run code on the site.
