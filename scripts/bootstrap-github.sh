#!/usr/bin/env bash
set -euo pipefail

REPO="codewithwest/project_ginger-night-walker"
VISIBILITY="${1:-private}"

if [[ "$VISIBILITY" != "private" && "$VISIBILITY" != "public" ]]; then
  echo "Usage: $0 [private|public]" >&2
  exit 2
fi

command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
command -v gh >/dev/null || { echo "GitHub CLI (gh) is required: https://cli.github.com/" >&2; exit 1; }
gh auth status >/dev/null || { echo "Run: gh auth login" >&2; exit 1; }

if [[ ! -d .git ]]; then
  git init -b main
fi

git add .
if ! git diff --cached --quiet; then
  git commit -m "Initial Ginger Night Walker scaffold"
fi

if gh repo view "$REPO" >/dev/null 2>&1; then
  echo "Repository already exists: https://github.com/$REPO"
  if ! git remote get-url origin >/dev/null 2>&1; then
    git remote add origin "https://github.com/$REPO.git"
  fi
else
  gh repo create "$REPO" "--$VISIBILITY" --source=. --remote=origin --push
fi

git push -u origin main
echo "Repository ready: https://github.com/$REPO"
