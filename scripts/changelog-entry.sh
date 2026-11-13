#!/usr/bin/env bash
# Prints the CHANGELOG.md entry for a tag (v0.3.0-rc1 -> "## 0.3.0-rc1 - date"), without its heading.
# Prints nothing if there is no entry. The Release workflow puts this at the top of the release notes.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
version="${1:?usage: changelog-entry.sh TAG}"
version="${version#v}"
awk -v want="## $version - " '
    index($0, want) == 1 { on = 1; next }
    /^## / && on { exit }
    on { lines[++n] = $0 }
    END {
        first = 1; while (first <= n && lines[first] == "") first++
        while (n >= first && lines[n] == "") n--
        for (i = first; i <= n; i++) print lines[i]
    }' CHANGELOG.md
