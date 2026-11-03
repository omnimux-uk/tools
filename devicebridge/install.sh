#!/usr/bin/env bash
# Set devicebridge up in place: check what this machine can actually run,
# make the virtualenv the iOS side requires, and say plainly what is missing.
#
# Nothing here touches anything outside this directory. Missing prerequisites
# are reported with the command that would install them, never installed.
set -euo pipefail

cd "$(dirname "$0")"
HERE="$(pwd)"
VENV="$HERE/.venv"

ios=1
android=1
case "${1:-all}" in
  all)     ;;
  ios)     android=0 ;;
  android) ios=0 ;;
  *) echo "usage: ./install.sh [all|ios|android]" >&2; exit 2 ;;
esac

say()  { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

# Each target says which systems it can run on at all, and the installer
# refuses rather than letting the checks fail one by one with advice that
# cannot be followed. iOS simulators are a macOS framework; there is no
# version of this that works elsewhere.
OS="$(uname -s)"
case "$OS" in
  Darwin)  HINT_ADB="brew install android-platform-tools"
           HINT_SCRCPY="brew install scrcpy"
           HINT_PY="brew install python@3.12" ;;
  Linux)   HINT_ADB="apt install android-tools-adb   (or dnf/pacman equivalent)"
           HINT_SCRCPY="apt install scrcpy   (or snap install scrcpy)"
           HINT_PY="apt install python3.12-venv" ;;
  *)       HINT_ADB="install the Android SDK platform-tools"
           HINT_SCRCPY="see github.com/Genymobile/scrcpy"
           HINT_PY="install Python 3.9-3.13" ;;
esac

missing=0
need() {  # need <command> <what to do about it>
  if command -v "$1" >/dev/null 2>&1; then
    ok "$1"
  else
    bad "$1 not found — $2"
    missing=1
  fi
}

echo
echo "devicebridge install  ($OS)"
echo

if [ "$OS" != "Darwin" ] && [ "$ios" = 1 ]; then
  if [ "${1:-all}" = "ios" ]; then
    bad "iOS simulators exist only on macOS — nothing to install on $OS"
    exit 1
  fi
  warn "iOS simulators exist only on macOS; skipping the iOS half"
  ios=0
fi

# fb-idb pins old grpclib/protobuf and does not build on the newest releases,
# so pick a known-good interpreter rather than whatever `python3` happens to
# be. On the machine this was written on `python3` was 3.15, whose venv could
# not even bootstrap pip. Anything 3.9-3.13 works; $PYTHON overrides.
pick_python() {
  if [ -n "${PYTHON:-}" ]; then echo "$PYTHON"; return; fi
  for c in python3.12 python3.11 python3.10 python3.13 python3.9 \
           /usr/bin/python3 python3; do
    p="$(command -v "$c" 2>/dev/null)" || continue
    if "$p" -c 'import sys; raise SystemExit(0 if (3,9) <= sys.version_info < (3,14) else 1)' 2>/dev/null; then
      echo "$p"; return
    fi
  done
}

echo "Python:"
PY="$(pick_python)"
if [ -z "$PY" ]; then
  bad "no Python between 3.9 and 3.13 found — fb-idb will not install on newer"
  say "$HINT_PY, or set PYTHON=/path/to/python3"
  exit 1
fi
ok "$("$PY" --version) at $PY"
echo

if [ "$ios" = 1 ]; then
  echo "iOS:"
  need xcrun "install Xcode and run: xcode-select --install"
  need idb_companion "brew tap facebook/fb && brew install idb-companion"
  # Homebrew 7 refuses third-party taps until they are trusted, and trusting
  # the one formula is narrower than trusting the whole tap.
  if ! command -v idb_companion >/dev/null 2>&1; then
    say "  (if brew refuses the tap: brew trust --formula facebook/fb/idb-companion)"
  fi
  echo
fi

if [ "$android" = 1 ]; then
  echo "Android:"
  need adb "$HINT_ADB, or use the Android SDK's platform-tools"
  need scrcpy "$HINT_SCRCPY"
  if command -v scrcpy >/dev/null 2>&1; then
    # Ask the module rather than keeping a second list of paths here -- the
    # two drifting apart is how you get an installer that passes and a bridge
    # that cannot start.
    jar="$("$PY" -c 'import sys; sys.path.insert(0, "."); from bridge.android import find_server_jar; print(find_server_jar() or "")')"
    if [ -n "$jar" ]; then
      ok "scrcpy-server ($jar)"
    else
      bad "scrcpy-server jar not found — set SCRCPY_SERVER_PATH to it"
      missing=1
    fi
  fi
  echo
fi

# The Android side is standard library only; the virtualenv exists for fb-idb,
# which the iOS side imports. Building it regardless keeps one way to run both.
if [ ! -d "$VENV" ]; then
  say "creating $VENV"
  "$PY" -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --disable-pip-version-check --upgrade pip
if [ "$ios" = 1 ]; then
  say "installing fb-idb (this takes a minute)"
  "$VENV/bin/pip" install -q --disable-pip-version-check -r "$HERE/requirements.txt"
  ok "fb-idb $("$VENV/bin/python" -c 'import idb,importlib.metadata as m; print(m.version("fb-idb"))' 2>/dev/null || echo installed)"
fi
echo

if [ "$missing" = 1 ]; then
  echo "Some prerequisites are missing — see the lines marked ✗ above."
  echo "devicebridge will still run for whichever target is complete."
  echo
fi

echo "Done. Start a bridge with:"
echo
# Written as full conditionals, not `[ x ] && echo`: under `set -e` a failing
# test is a failing command, and the script would exit non-zero right here
# whenever one of the two targets was skipped.
if [ "$ios" = 1 ]; then
  echo "  ./devicebridge ios        # http://127.0.0.1:9200"
fi
if [ "$android" = 1 ]; then
  echo "  ./devicebridge android    # http://127.0.0.1:9201"
fi
echo
echo "Then forward that port to this machine and open it in a browser."
