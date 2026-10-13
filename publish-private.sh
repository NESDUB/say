#!/bin/sh
# Upload exactly this project to a private repository in the active gh account.
set -eu
cd "$(dirname "$0")"
command -v gh >/dev/null
command -v git >/dev/null
gh auth status
if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
  echo 'Create the initial local commit before publishing.' >&2
  exit 1
fi
# If an earlier create succeeded but push failed, resume only a verified private repo.
if git remote get-url origin >/dev/null 2>&1; then
  visibility=$(gh repo view --json isPrivate --jq .isPrivate)
  [ "$visibility" = true ] || { echo 'Refusing to upload to a public repository.' >&2; exit 1; }
  git push -u origin main
else
  gh repo create say --private --description 'Portable local Kokoro speech with automatic model installation' --source=. --remote=origin --push
fi
gh repo view --json url,isPrivate --jq 'if .isPrivate then .url else error("Repository is not private") end'
