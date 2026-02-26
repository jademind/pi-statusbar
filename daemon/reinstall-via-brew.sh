#!/usr/bin/env bash
set -euo pipefail

FORMULA_FULL="jademind/tap/pi-statusbar"
FORMULA_NAME="pi-statusbar"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required but was not found in PATH."
  exit 1
fi

is_installed() {
  brew list --versions "$FORMULA_NAME" >/dev/null 2>&1
}

echo "==> Ensuring tap is available"
brew tap jademind/tap >/dev/null

if is_installed; then
  echo "==> Existing $FORMULA_NAME installation found. Removing for clean reinstall..."

  # Best-effort stop for both Homebrew service and user LaunchAgents.
  brew services stop "$FORMULA_NAME" >/dev/null 2>&1 || true
  if command -v statusbar-setup >/dev/null 2>&1; then
    statusbar-setup stop --remove yes >/dev/null 2>&1 || true
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

echo "==> Installing fresh copy: brew install $FORMULA_FULL"
brew install "$FORMULA_FULL"

echo "==> Done. Installed version:"
brew list --versions "$FORMULA_NAME"
