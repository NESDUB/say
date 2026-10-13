#!/bin/sh
# Linux/macOS bootstrap; run as your normal user, not sudo.
set -eu
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$(dirname "$0")/install.py" "$@"
fi
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip
elif command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y python3 python3-pip
elif command -v pacman >/dev/null 2>&1; then
  sudo pacman -S --needed --noconfirm python python-pip
elif command -v zypper >/dev/null 2>&1; then
  sudo zypper --non-interactive install python3 python3-pip
elif command -v brew >/dev/null 2>&1; then
  brew install python@3.12
else
  echo 'Install Python 3.10+ (3.12 recommended), then rerun this installer.' >&2
  exit 1
fi
exec python3 "$(dirname "$0")/install.py" "$@"
