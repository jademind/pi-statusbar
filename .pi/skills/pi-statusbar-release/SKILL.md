---
name: pi-statusbar-release
description: End-to-end release workflow for pi-statusbar, including mode selection, version/tag releases, formula-only revisions, tap sync, and post-release verification.
---

# Pi Statusbar Release Skill

Use this skill when releasing `pi-statusbar` (daemon + app + Homebrew distribution).

## Scope

This skill covers:
- source releases in `jademind/pi-statusbar`
- Homebrew formula updates in this repo and `jademind/homebrew-tap`
- install/startup verification (`pi-statusbar` unified CLI)

See full checklist: [RELEASE-CHECKLIST.md](./RELEASE-CHECKLIST.md)
After selecting a mode, execute only that mode’s checklist section and treat every unchecked item as a release blocker.

---

## 0) Select Release Mode (required)

Pick the mode from changed files:

### Mode A — New upstream version release
Use when **source/runtime code changed**, including any change in:
- `daemon/`
- app code (`PiStatusBar*`, `Sources/`, etc.)
- scripts/binaries shipped in source tarball and executed at runtime

➡ This creates a **new version + git tag** and updates formulas to that new tarball.

### Mode B — Formula-only revision release
Use when **only packaging changed**, e.g.:
- Homebrew formula install layout
- plist/service wiring
- launch/setup scripts in formula packaging

and upstream source tarball/version stays the same.

➡ Keep `version`/`url` fixed, bump `revision`.

### Mode C — Tap-sync only
Use when formula in `jademind/pi-statusbar` is already correct, and only `jademind/homebrew-tap` still needs the same formula state.

➡ No new app version; copy formula changes into tap and publish.

### Mode D — No release
Use when changes are docs/tests/internal only and nothing user-installable changed.

➡ No tag/formula update required.

---

## Commit Message Format (required)

Use a **one-line subject** plus a **detailed body** for release-related commits.

Template:

```bash
git commit \
  -m "<short one-line subject>" \
  -m "<detailed description: what changed, why, and any follow-up notes>"
```

Examples:

```bash
git commit \
  -m "Release vX.Y.Z" \
  -m "- Bump VERSION and README release marker\n- Update Formula URL/version/sha256 for vX.Y.Z\n- Prepare tag vX.Y.Z and release publication"
```

---

## A) New Upstream Version Release Procedure

1. **Choose next version** `X.Y.Z` and update:
   - `VERSION`
   - `README.md` (latest tagged release / release notes section)

2. **Update formula in this repo** (`Formula/pi-statusbar.rb`):
   - `url` -> `https://github.com/jademind/pi-statusbar/archive/refs/tags/vX.Y.Z.tar.gz`
   - `version "X.Y.Z"`
   - `sha256` -> new tarball hash
   - remove/reset `revision` (unless immediately needed for packaging-only follow-up)

3. **Commit + tag + push source release**
```bash
git add VERSION README.md Formula/pi-statusbar.rb
git commit \
  -m "Release vX.Y.Z" \
  -m "- Bump VERSION and README release marker\n- Update Formula URL/version/sha256 for vX.Y.Z\n- Prepare source tag and Homebrew publication"
git tag vX.Y.Z
git push origin main --tags
```

4. **Create and hash release tarball**
```bash
curl -L -o /tmp/pi-statusbar-vX.Y.Z.tar.gz \
  https://github.com/jademind/pi-statusbar/archive/refs/tags/vX.Y.Z.tar.gz
shasum -a 256 /tmp/pi-statusbar-vX.Y.Z.tar.gz
```

> If hash differs from formula, update `sha256`, amend commit if needed, and push.

5. **Publish GitHub release** for `vX.Y.Z` with release notes.

6. **Update tap repo** (`jademind/homebrew-tap`):
   - copy same `url/version/sha256` (and revision state) into `Formula/pi-statusbar.rb`
   - commit + push

7. **Verify install/upgrade**
```bash
brew update
brew upgrade jademind/tap/pi-statusbar || brew install jademind/tap/pi-statusbar
pi-statusbar enable
pi-statusbar status
pi-statusbar daemon-service-status
pi-statusbar daemon-ping
pi-statusbar app-status
```

8. **Optional clean reinstall QA**
```bash
./daemon/reinstall-via-brew.sh
```

---

## B) Formula-Only Revision Procedure

Use when packaging changed but source tag is unchanged.

1. In formula (`Formula/pi-statusbar.rb`):
   - keep `version` + tarball `url` unchanged
   - increment `revision` (e.g. `revision 2`)

2. Commit + push formula update in `jademind/pi-statusbar` using the required commit format (one-line subject + detailed body).

3. Mirror same revision bump in `jademind/homebrew-tap/Formula/pi-statusbar.rb` and commit with the same format.

4. Validate with:
```bash
brew update
brew upgrade jademind/tap/pi-statusbar
```

---

## C) Tap-Sync Only Procedure

1. Copy current canonical formula state into tap formula:
   - `jademind/pi-statusbar/Formula/pi-statusbar.rb` -> `jademind/homebrew-tap/Formula/pi-statusbar.rb`
2. Commit + push tap repo using the required commit format (one-line subject + detailed body).
3. Run:
```bash
brew update
brew upgrade jademind/tap/pi-statusbar || brew install jademind/tap/pi-statusbar
```

---

## Must-Pass Checks Before Announcing Release

- `brew install jademind/tap/pi-statusbar` succeeds
- `pi-statusbar enable` succeeds
- menu bar icon appears quickly (no runtime `swift run` compile path)
- `pi-statusbar daemon-ping` returns healthy response
- `pi-statusbar app-status` shows registered/loaded/running

## Notes

- Startup should run compiled binary directly.
- Compilation should happen during `brew install`/`brew upgrade` only.
- If local repo install is used, one-time build is required:
```bash
swift build -c release --product PiStatusBar
```
