# Pi Statusbar Release Checklist

## 0) Select release mode (pick one)
- [ ] **Mode A — New upstream version release** (required for code/runtime changes, including `daemon/`)
- [ ] **Mode B — Formula-only revision release** (packaging-only, same source tag)
- [ ] **Mode C — Tap-sync only** (sync tap formula to canonical repo formula)
- [ ] **Mode D — No release** (docs/tests/internal-only changes)

---

## 1) Common preflight
- [ ] Confirm branch is clean
- [ ] Confirm scope of changed files matches selected mode
- [ ] Validate formula syntax when formula changed: `ruby -c Formula/pi-statusbar.rb`

---

## 2A) Mode A — New upstream version release

### Source/version
- [ ] Confirm target version `X.Y.Z`
- [ ] Update `VERSION`
- [ ] Update latest release text in `README.md`

### Formula (repo)
- [ ] Update `Formula/pi-statusbar.rb`:
  - [ ] `url` -> `.../refs/tags/vX.Y.Z.tar.gz`
  - [ ] `version "X.Y.Z"`
  - [ ] `sha256` -> new tarball hash
  - [ ] `revision` removed/reset (unless intentionally needed)

### Git/GitHub release
- [ ] Commit release changes
- [ ] Create tag `vX.Y.Z`
- [ ] Push branch + tags
- [ ] Publish GitHub release notes

### Homebrew tap
- [ ] Update `jademind/homebrew-tap/Formula/pi-statusbar.rb` with same `url/version/sha256` (+ revision state)
- [ ] Commit + push tap update

### Verification
- [ ] `brew update`
- [ ] `brew install` or `brew upgrade jademind/tap/pi-statusbar` works
- [ ] `pi-statusbar enable` works
- [ ] `pi-statusbar status` healthy
- [ ] `statusd-service status` healthy
- [ ] `statusdctl ping` healthy
- [ ] `statusbar-app-service status` healthy
- [ ] Menu bar icon appears quickly (no runtime compile delay)

---

## 2B) Mode B — Formula-only revision release

### Formula (repo)
- [ ] Keep source `url` unchanged
- [ ] Keep `version` unchanged
- [ ] Increment `revision` in `Formula/pi-statusbar.rb`
- [ ] Commit + push repo formula update

### Homebrew tap
- [ ] Mirror same `revision` bump in `jademind/homebrew-tap/Formula/pi-statusbar.rb`
- [ ] Commit + push tap update

### Verification
- [ ] `brew update`
- [ ] `brew upgrade jademind/tap/pi-statusbar` works
- [ ] `pi-statusbar status` healthy
- [ ] `statusdctl ping` healthy

---

## 2C) Mode C — Tap-sync only
- [ ] Copy canonical formula from this repo to tap formula
- [ ] Commit + push tap update
- [ ] `brew update`
- [ ] `brew install` or `brew upgrade jademind/tap/pi-statusbar` works

---

## 2D) Mode D — No release
- [ ] Confirm no user-installable/runtime changes
- [ ] Confirm no formula/tap updates needed
- [ ] Document decision in PR/release notes (optional)

---

## 3) Optional clean reinstall QA (recommended for A/B)
- [ ] `./daemon/reinstall-via-brew.sh`
- [ ] Re-check service + app status
