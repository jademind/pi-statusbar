#!/usr/bin/env bash
set -euo pipefail

TAP_FULL="jademind/tap"
FORMULA_NAME="pi-statusbar"
FORMULA_FULL="$TAP_FULL/$FORMULA_NAME"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_FORMULA="$REPO_ROOT/Formula/${FORMULA_NAME}.rb"

MODE=""
RESTORE_REMOTE="${RESTORE_REMOTE:-no}" # only used in --local mode: yes|no

usage() {
  cat <<EOF
Usage: $0 [--local|--remote] [--help]

Options:
  --local    Reinstall from the local repository via a custom tap remote
  --remote   Reinstall from jademind/tap on GitHub
  --help     Show this help

If no option is provided, the script asks you to choose a mode.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --local)
      MODE="local"
      ;;
    --remote)
      MODE="remote"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $arg"
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$MODE" ]]; then
  echo "==> Reinstall mode selection"
  echo "This script can reinstall pi-statusbar from either:"
  echo "  1) remote tap ($TAP_FULL on GitHub)"
  echo "  2) local repo ($REPO_ROOT)"
  echo
  read -r -p "Select mode [1=remote, 2=local, Enter=1]: " choice
  case "${choice:-1}" in
    1) MODE="remote" ;;
    2) MODE="local" ;;
    *)
      echo "Invalid selection: $choice"
      exit 1
      ;;
  esac
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required but was not found in PATH."
  exit 1
fi

is_installed() {
  brew list --versions "$FORMULA_NAME" >/dev/null 2>&1
}

PREV_REMOTE=""
if [[ "$MODE" == "local" ]]; then
  if [[ ! -f "$LOCAL_FORMULA" ]]; then
    echo "Local formula not found: $LOCAL_FORMULA"
    exit 1
  fi

  if brew tap | grep -qx "$TAP_FULL"; then
    TAP_REPO="$(brew --repo "$TAP_FULL")"
    PREV_REMOTE="$(git -C "$TAP_REPO" remote get-url origin 2>/dev/null || true)"
  fi

  echo "==> Pointing $TAP_FULL to local repository"
  brew tap --custom-remote "$TAP_FULL" "$REPO_ROOT"

  # Ensure Homebrew uses the exact local working-copy formula (including uncommitted changes).
  TAP_REPO="$(brew --repo "$TAP_FULL")"
  mkdir -p "$TAP_REPO/Formula"
  cp "$LOCAL_FORMULA" "$TAP_REPO/Formula/${FORMULA_NAME}.rb"
  echo "==> Synced local formula into tap: $TAP_REPO/Formula/${FORMULA_NAME}.rb"
else
  echo "==> Ensuring tap is available"
  brew tap "$TAP_FULL" >/dev/null
fi

if is_installed; then
  echo "==> Existing $FORMULA_NAME installation found. Removing for clean reinstall..."

  # Best-effort stop for both Homebrew service and user LaunchAgents.
  brew services stop "$FORMULA_NAME" >/dev/null 2>&1 || true
  if command -v pi-statusbar >/dev/null 2>&1; then
    pi-statusbar stop --remove yes >/dev/null 2>&1 || true
  fi

  deps=()
  while IFS= read -r dep; do
    [[ -n "$dep" ]] && deps+=("$dep")
  done < <(brew deps --installed "$FORMULA_NAME" 2>/dev/null || true)

  brew uninstall --force "$FORMULA_NAME"

  if ((${#deps[@]} > 0)); then
    echo "==> Removing installed dependencies: ${deps[*]}"
    brew uninstall --force "${deps[@]}" >/dev/null 2>&1 || true
  fi

  # Remove now-unused dependencies and old artifacts.
  brew autoremove >/dev/null 2>&1 || true
  brew cleanup >/dev/null 2>&1 || true
else
  echo "==> $FORMULA_NAME is not currently installed. Proceeding with fresh install."
fi

if [[ "$MODE" == "local" ]]; then
  echo "==> Installing fresh copy (local mode, auto-update disabled):"
  echo "    HOMEBREW_NO_AUTO_UPDATE=1 brew install --build-from-source $FORMULA_FULL"
  HOMEBREW_NO_AUTO_UPDATE=1 brew install --build-from-source "$FORMULA_FULL"
else
  echo "==> Installing fresh copy: brew install --build-from-source $FORMULA_FULL"
  brew install --build-from-source "$FORMULA_FULL"
fi

echo "==> Done. Installed version:"
brew list --versions "$FORMULA_NAME"

if [[ "$MODE" == "local" && -n "$PREV_REMOTE" ]]; then
  if [[ "$RESTORE_REMOTE" == "yes" ]]; then
    echo "==> Restoring original tap remote: $PREV_REMOTE"
    brew tap --custom-remote "$TAP_FULL" "$PREV_REMOTE"
  else
    echo "==> Note: $TAP_FULL currently points to local repo: $REPO_ROOT"
    echo "   To restore the previous remote later, run:"
    echo "   brew tap --custom-remote $TAP_FULL $PREV_REMOTE"
  fi
fi
