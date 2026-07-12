# התערבות מונדיאל 2026 — אתר חי

A read-only website for our World Cup 2026 prediction pool: live leaderboard, golden-boot tracker,
results, and a browsable gallery of everyone's bracket. It updates itself every morning.

## How it works
- `index.html` — the whole site (self-contained, data embedded). This is what gets served.
- `update.py` — fetches actual results from ESPN's public JSON, scores every participant per our
  rules, and rebuilds `site_data.json` + `index.html`. No third-party libraries.
- `predictions.json` — everyone's predictions (static; never changes).
- `results_store.json` — actual results so far (updated automatically; editable by hand if a score is ever wrong).
- `.github/workflows/update.yml` — runs `update.py` every morning and commits the refresh.

## One-time setup
1. **Create a repo** (e.g. `mundial-2026`) and upload all these files (keep the folder structure,
   including `.github/workflows/update.yml`).
2. **Add the site password** as a secret so it isn't stored in the code:
   repo → Settings → Secrets and variables → Actions → New repository secret →
   name `GATE_PASSWORD`, value = the shared password.
3. **Enable Pages:** Settings → Pages → Source = "Deploy from a branch" → Branch `main` / `(root)`.
   Your site will be at `https://<user>.github.io/<repo>/`.
4. *(optional)* **Custom domain:** Settings → Pages → Custom domain → enter your domain, then add a
   CNAME DNS record at your registrar pointing to `<user>.github.io`. A `CNAME` file will be created.
5. **First run:** Actions tab → "Update Mundial site" → Run workflow (or just wait for the morning run).

## Scoring rules
Correct direction = 1 · exact score = 2 · exact score with 4+ goals = 3 ·
advancing R32/R16/QF/SF/Final/Champion = 4/5/7/10/14/16 per team · golden boot = 12 after the final is played.
Match-result points are based on the score after 90 minutes, excluding extra time and penalty shootouts.

## Fixing a wrong score
Edit `results_store.json` (set the match's `home_score`/`away_score`), commit. The next run rebuilds.
For a knockout match that went to extra time, set `home_score_90`/`away_score_90` as well.
Golden-boot leaders can be displayed live, but golden-boot points are awarded only after the Final is played.
If needed, set `golden_boot_leaders` in `results_store.json` near the end of the tournament.
