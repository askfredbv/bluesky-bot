# Releasing

How a version of the bot gets cut. The bot runs from `main`, so a release changes no behaviour. It names a point in history, puts the right version in every run log, and gives a readable account of what changed.

Written on 2026-09-11 from how v4.24.0 to v4.26.0 were actually cut. v4.27.0 is its first use. Until then releases were done from memory, and the wiki fell three releases behind.

---

## 0. Before you start

- [ ] **`main` is green.** Its own CI passed on the last merge commit:
  `gh run list --commit $(git rev-parse origin/main)`
- [ ] **Nothing meant for this release is still an open PR.**
- [ ] **The changes have run in production.** At least one scheduled run on the release's code has finished, and its output was read: the posts on both platforms and the run log. Note which changes that run actually exercised. The release notes separate what was **live-verified** from what is only **test-covered**. A change no run could exercise goes in the second list, not the first.
- [ ] **Pick the version.** Minor (4.26.0 → 4.27.0) when readers of the feed can see a difference. Patch (4.25.0 → 4.25.1) for fixes alone.
- [ ] **List what is in it:** `git log --oneline vPREVIOUS..origin/main`

## 1. The release PR

Branch from a freshly fetched `origin/main`, never from a local `main`:

```bash
git fetch origin && git checkout -b docs/release-vX.Y.Z origin/main
```

- [ ] **Bump the version in the four places it lives:**

  | File | Where |
  | --- | --- |
  | `README.md` | the title: `# Bluesky & Mastodon Daily Poster (vX.Y.Z)` |
  | `main.py` | the startup banner: `AskFred Engine vX.Y.Z` in the `run_started` log line |
  | `pyproject.toml` | `[project] version` |
  | `docs/BACKLOG.md` | the header paragraph: put the new release in front, in one paragraph |

- [ ] **Add a changelog line** at the end of `docs/BACKLOG.md`: the date, **vX.Y.Z — theme**, what changed and why.
- [ ] **Check nothing current still says the old version.** Only history may remain, in the BACKLOG header and changelog:
  `git grep -n "OLD.VERSION"`
- [ ] **Title the PR `docs(release): vX.Y.Z — <theme>`.** It merges like any other PR, squashed, once the required checks (`tests`, `lockfile`) pass.

## 2. Tag and GitHub release

- [ ] **Tag the squash commit the release PR produced**, as an annotated tag:

  ```bash
  git fetch origin
  git tag -a vX.Y.Z <merge-sha> -m "vX.Y.Z — <theme>" -m "<two or three lines: what changed>"
  git push origin vX.Y.Z
  ```

- [ ] **Create the GitHub release on that tag**, with the same name:

  ```bash
  gh release create vX.Y.Z --verify-tag --title "vX.Y.Z — <theme>" --notes-file notes.md
  ```

- [ ] **Write the notes in this order:**
  1. What changed, by theme and in plain words: first what a reader of the feed sees, then fixes, then internals.
  2. **Live-verified:** what the production run showed, with the evidence (a post, a log line).
  3. **Test-covered only:** what no run could exercise yet, and why.
  4. Known limits, and the rollback switch if there is one.

## 3. The wiki

The wiki is a separate repository, `askfredbv/bluesky-bot.wiki`. It has no PR flow: changes are pushed straight to `master`. Nothing reminds anyone to update it, which is why this step is on the list.

- [ ] **Clone it, or pull an existing clone:**
  `git clone https://github.com/askfredbv/bluesky-bot.wiki.git`
- [ ] **Update the Version line on `Home.md`**, and the test count if it changed.
- [ ] **Fix every page the release made wrong.** The usual suspects:
  - `Architecture.md`: pipeline stages, file tree, state files.
  - `Configuration.md`: constants and their defaults.
  - `Troubleshooting.md`: log events and failure behaviour.
  - `Content-Modes.md` and `Home.md`: the feed count.
- [ ] **Commit, push, and check the published page**, not your clone:
  `https://raw.githubusercontent.com/wiki/askfredbv/bluesky-bot/Home.md`

## 4. After

- [ ] **`main`'s own CI passed** on the release commit.
- [ ] **The release is listed as Latest:** `gh release list --limit 1`
- [ ] **The next scheduled run logs `AskFred Engine vX.Y.Z`** in its `run_started` line. That is the proof that production runs the release.
