#!/usr/bin/env bash
# Run the same CodeQL analysis as .github/workflows/codeql.yml, locally, before pushing.
#   scripts/codeql.sh setup   download the CodeQL bundle (once; ~1 GB)
#   scripts/codeql.sh [run]   analyse python and actions; exit 1 if anything is found
# CODEQL_HOME moves the install (default ~/.local/share/codeql, outside the repository).
# CODEQL_VERSION pins the bundle tag (say codeql-bundle-v2.27.4); empty means the latest.
# Databases and SARIF results go to .dev/codeql (gitignored).
set -euo pipefail

cd "$(dirname "$0")/.."
HOME_DIR="${CODEQL_HOME:-$HOME/.local/share/codeql}"
OUT=.dev/codeql
LANGUAGES=(python actions)

codeql_bin() {
  if [ -x "$HOME_DIR/codeql/codeql" ]; then echo "$HOME_DIR/codeql/codeql"
  elif command -v codeql >/dev/null; then command -v codeql
  else echo "codeql: not installed; run scripts/codeql.sh setup" >&2; return 1; fi
}

setup() {
  [ -x "$HOME_DIR/codeql/codeql" ] && { echo "codeql: already installed in $HOME_DIR"; "$HOME_DIR/codeql/codeql" version --format=terse; return; }
  command -v gh >/dev/null || { echo "codeql: needs the gh CLI to download" >&2; exit 1; }
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) asset=codeql-bundle-linux64.tar.zst ;;
    Darwin-*) asset=codeql-bundle-osx64.tar.zst ;;
    *) echo "codeql: no bundle for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
  esac
  # Download into a fresh directory and extract it there, then move the result into place.
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  # The bundle (CLI plus query packs) is published from codeql-action, not codeql-cli-binaries.
  tag="${CODEQL_VERSION:-$(gh release list -R github/codeql-action -L 30 --json tagName -q '.[].tagName' | grep -m1 '^codeql-bundle-')}"
  gh release download "$tag" -R github/codeql-action -p "$asset" -p "$asset.checksum.txt" -D "$tmp"
  (cd "$tmp" && sha256sum -c "$asset.checksum.txt" 2>/dev/null || [ "$(cut -d' ' -f1 "$asset.checksum.txt")" = "$(sha256sum "$asset" | cut -d' ' -f1)" ]) \
    || { echo "codeql: checksum mismatch" >&2; exit 1; }
  mkdir "$tmp/x"
  tar --zstd -xf "$tmp/$asset" -C "$tmp/x"
  mkdir -p "$HOME_DIR"
  mv "$tmp/x/codeql" "$HOME_DIR/codeql"
  "$HOME_DIR/codeql/codeql" version --format=terse
}

run() {
  codeql=$(codeql_bin)
  rm -rf "$OUT"; mkdir -p "$OUT"
  found=0
  for lang in "${LANGUAGES[@]}"; do
    echo "== CodeQL: $lang"
    "$codeql" database create "$OUT/db-$lang" --language="$lang" --build-mode=none --source-root=. --overwrite --threads=0 >"$OUT/$lang.log" 2>&1
    "$codeql" database analyze "$OUT/db-$lang" "codeql/$lang-queries:codeql-suites/$lang-code-scanning.qls" \
      --format=sarif-latest --output="$OUT/$lang.sarif" --sarif-category="/language:$lang" --threads=0 --quiet
    n=$(python3 -I - "$OUT/$lang.sarif" <<'PY'
import json, sys
results = [r for run in json.load(open(sys.argv[1]))["runs"] for r in run["results"]]
for r in results:
    loc = r["locations"][0]["physicalLocation"]
    print(f'  {loc["artifactLocation"]["uri"]}:{loc["region"]["startLine"]}  {r["ruleId"]}', file=sys.stderr)
print(len(results))
PY
)
    echo "$lang: $n finding(s)"
    [ "$n" = 0 ] || found=1
  done
  [ "$found" = 0 ] && echo "CodeQL: clean" || { echo "CodeQL: findings above; SARIF in $OUT" >&2; exit 1; }
}

case "${1:-run}" in
  setup) setup ;;
  run) run ;;
  *) echo "usage: $0 [setup|run]" >&2; exit 2 ;;
esac
