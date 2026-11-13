#!/usr/bin/env bash
# =============================================================================
# teslamate-listing.sh — our entry on TeslaMate's "Projects using TeslaMate" page.
#
#   scripts/teslamate-listing.sh check     show how TeslaMate's page differs from
#                                          .github/teslamate-listing.md (exit 1 if it does)
#   scripts/teslamate-listing.sh propose   open, or refresh, the pull request on
#                                          teslamate-org/teslamate that fixes it
#
# `propose` publishes under the GitHub account of whoever runs it (or GH_TOKEN's): it forks
# TeslaMate, pushes one branch to the fork and opens a pull request, so run it deliberately.
# It is step 7 of RELEASING.md, run by hand, never from CI. Re-running updates
# the same branch and pull request rather than opening another.
# =============================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

UPSTREAM=teslamate-org/teslamate
PAGE=website/docs/projects.md
BRANCH=update-teslamate-expenses-listing
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fetch_page() { gh api "repos/$UPSTREAM/contents/$PAGE" --jq .content | base64 -d > "$1"; }

case "${1:-}" in
    check)
        fetch_page "$work/upstream.md"
        python3 -I scripts/teslamate_listing.py merge "$work/upstream.md" > "$work/new.md"
        if diff -u --label "TeslaMate's page" --label "with our entry" "$work/upstream.md" "$work/new.md"; then
            echo "listing: TeslaMate's page already matches .github/teslamate-listing.md"
        else
            echo "listing: TeslaMate's page differs (diff above). 'propose' opens the pull request." >&2
            exit 1
        fi ;;
    propose)
        me="$(gh api user --jq .login)"
        gh repo fork "$UPSTREAM" --clone=false >/dev/null 2>&1 || true   # already forked is fine
        gh repo sync "$me/teslamate" --source "$UPSTREAM" >/dev/null
        # git gets its password from gh (GH_TOKEN, or the account gh is logged in to)
        auth=(-c 'credential.helper=!gh auth git-credential')   # for these commands only
        git "${auth[@]}" clone -q --depth 1 "https://github.com/$me/teslamate.git" "$work/fork"
        python3 -I scripts/teslamate_listing.py merge "$work/fork/$PAGE" > "$work/new.md"
        cp "$work/new.md" "$work/fork/$PAGE"
        cd "$work/fork"
        if git diff --quiet; then
            echo "listing: nothing to propose, TeslaMate's page already matches"
            exit 0
        fi
        git checkout -q -B "$BRANCH"
        git -c "user.name=$me" -c "user.email=$me@users.noreply.github.com" commit -q -am "Update the TeslaMate Expenses entry in projects.md"
        git "${auth[@]}" push -q --force origin "$BRANCH"
        existing="$(gh pr list -R "$UPSTREAM" --head "$me:$BRANCH" --state open --json number --jq '.[0].number // empty')"
        if [ -n "$existing" ]; then
            echo "listing: updated the open pull request $UPSTREAM#$existing"
        else
            gh pr create -R "$UPSTREAM" --head "$me:$BRANCH" --base main \
                --title "Update the TeslaMate Expenses entry in projects.md" \
                --body "Keeps the entry for [teslamate-expenses](https://github.com/mattew124/teslamate-expenses) describing what the current release does. It reads TeslaMate's database directly, as the page's introduction says."
        fi ;;
    *) sed -n '2,13p' "$0"; exit 2 ;;
esac
