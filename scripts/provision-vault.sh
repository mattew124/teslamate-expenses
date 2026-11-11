#!/usr/bin/env bash
# =============================================================================
# provision-vault.sh — OPTIONAL. Sets Vault up so the app can read its OneDrive encryption
# key itself (docs, "OneDrive uploads"; VAULT_* settings). It asks for a Vault token, which
# needs rights to write policies and auth roles and to write the secret; nothing else.
#
# It then:
#   1. enables the AppRole auth method, if it isn't on yet;
#   2. writes a read-only policy for the one secret;
#   3. creates (or updates) an AppRole holding that policy;
#   4. creates the secret with a fresh random key, but only if it doesn't exist (an
#      existing key is never overwritten: that would lock the saved OneDrive sign-in);
#   5. issues a new secret ID and writes the app's VAULT_* lines to a file (mode 0600),
#      never to the screen. Add them to the app's .env.
#
# Idempotent. Running it again keeps the key and issues another secret ID; secret IDs
# issued earlier keep working until they expire (see SECRET_ID_TTL).
#
# The token is asked for on the terminal without echo (or read from the first line of
# stdin when that isn't a terminal). It is never an argument or an environment variable,
# where `ps` or a log could show it, and it is not saved.
#
#   VAULT_ADDR=https://vault.example:8200 ./scripts/provision-vault.sh
#
# Environment:
#   VAULT_ADDR         required
#   VAULT_MOUNT        KV version 2 mount, which must already exist   (default secret)
#   VAULT_SECRET_PATH  where the key lives under the mount   (default teslamate-expenses-onedrive)
#   VAULT_SECRET_FIELD field holding the key                        (default encryption_key)
#   VAULT_ROLE         AppRole and policy name                      (default teslamate-expenses)
#   VAULT_NAMESPACE    Enterprise namespace, if any
#   VAULT_CACERT       CA bundle for a private certificate authority
#   SECRET_ID_TTL      how long the new secret ID lasts             (default 0: no expiry)
#   OUT_FILE           where the VAULT_* lines go                   (default ./expenses-vault.env)
# =============================================================================
set -euo pipefail

ADDR="${VAULT_ADDR:-}"
MOUNT="${VAULT_MOUNT:-secret}"
SPATH="${VAULT_SECRET_PATH:-teslamate-expenses-onedrive}"
FIELD="${VAULT_SECRET_FIELD:-encryption_key}"
ROLE="${VAULT_ROLE:-teslamate-expenses}"
SID_TTL="${SECRET_ID_TTL:-0}"
OUT="${OUT_FILE:-./expenses-vault.env}"

die() { echo "provision-vault: $*" >&2; exit 1; }

[[ -n "$ADDR" ]] || die "VAULT_ADDR is not set"
ADDR="${ADDR%/}"
for tool in curl python3 openssl; do command -v "$tool" >/dev/null || die "$tool is required"; done
[[ "$MOUNT" =~ ^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$ ]] || die "VAULT_MOUNT has characters Vault paths don't take"
[[ "$SPATH" =~ ^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$ ]] || die "VAULT_SECRET_PATH has characters this script won't pass on"
[[ "$ROLE" =~ ^[A-Za-z0-9_-]+$ ]] || die "VAULT_ROLE must be letters, digits, - or _"
[[ "$FIELD" =~ ^[A-Za-z0-9_-]+$ ]] || die "VAULT_SECRET_FIELD must be letters, digits, - or _"
[[ "$SID_TTL" =~ ^[0-9]+[smh]?$ ]] || die "SECRET_ID_TTL looks like 0, 3600 or 720h"
[[ ! -e "$OUT" ]] || die "$OUT already exists: move it or choose another OUT_FILE (it holds a secret ID)"

if [[ -t 0 ]]; then
    read -rsp "Vault token (needs policy, auth and secret rights; typing is hidden): " TOKEN; echo >&2
else
    IFS= read -r TOKEN || true
fi
[[ -n "$TOKEN" ]] || die "no token given"

CODE="" BODY=""
# vault METHOD PATH [JSON] -> response body in $BODY, HTTP status in $CODE (globals: a $(...) would
# lose them). The token travels in a
# header read from a descriptor, so it is in no argument list.
vault() {
    local method="$1" path="$2" body="${3-}" out
    local args=(-sS -X "$method" --max-time 15)
    [[ -z "${VAULT_NAMESPACE:-}" ]] || args+=(-H "X-Vault-Namespace: $VAULT_NAMESPACE")
    [[ -z "${VAULT_CACERT:-}" ]] || args+=(--cacert "$VAULT_CACERT")
    if [[ -n "$body" ]]; then
        out="$(printf '%s' "$body" | curl "${args[@]}" --config <(printf 'header = "X-Vault-Token: %s"\n' "$TOKEN") -H 'Content-Type: application/json' --data-binary @- \
            -w '\n%{http_code}' "$ADDR/v1/$path")" || die "couldn't reach $ADDR"
    else
        out="$(curl "${args[@]}" --config <(printf 'header = "X-Vault-Token: %s"\n' "$TOKEN") -w '\n%{http_code}' "$ADDR/v1/$path")" || die "couldn't reach $ADDR"
    fi
    CODE="${out##*$'\n'}"
    BODY="${out%$'\n'*}"
}

# json_get 'expression on d' <<< json
json_get() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"; }

ok() { [[ "$CODE" == 2* ]] || die "$1 (HTTP $CODE)"; }

# 1. AppRole auth method
vault GET sys/auth
case "$CODE" in 200) ;; 403) die "the token can't read sys/auth: it needs rights to manage auth methods" ;; *) die "sys/auth (HTTP $CODE)" ;; esac
if ! json_get '"approle/" in d.get("data", d)' <<<"$BODY" | grep -qx True; then
    vault POST sys/auth/approle '{"type":"approle"}'; ok "couldn't enable the AppRole auth method"
    echo "provision-vault: AppRole auth method enabled"
fi

# 2. read-only policy for the one secret
policy="path \"$MOUNT/data/$SPATH\" { capabilities = [\"read\"] }"
vault PUT "sys/policies/acl/$ROLE" "$(python3 -c 'import json,sys; print(json.dumps({"policy": sys.argv[1]}))' "$policy")"
ok "couldn't write policy $ROLE"

# 3. the AppRole
vault POST "auth/approle/role/$ROLE" "$(python3 -c '
import json, sys
print(json.dumps({"token_policies": [sys.argv[1]], "token_ttl": "1h", "token_max_ttl": "4h",
                  "secret_id_ttl": sys.argv[2], "token_num_uses": 0, "secret_id_num_uses": 0}))' "$ROLE" "$SID_TTL")"
ok "couldn't write AppRole $ROLE"

# 4. the key, never overwritten
vault GET "$MOUNT/data/$SPATH"
if [[ "$CODE" == 200 ]]; then
    echo "provision-vault: $MOUNT/$SPATH already holds a key: left as it is"
else
    [[ "$CODE" == 404 ]] || die "couldn't look at $MOUNT/$SPATH (HTTP $CODE)"
    key="$(openssl rand -base64 32 | tr '+/' '-_')"
    vault POST "$MOUNT/data/$SPATH" "$(python3 -c '
import json, sys
print(json.dumps({"options": {"cas": 0}, "data": {sys.argv[1]: sys.argv[2]}}))' "$FIELD" "$key")"
    unset key
    [[ "$CODE" != 404 ]] || die "$MOUNT is not a KV version 2 mount here: create it first (vault secrets enable -path=$MOUNT kv-v2)"
    ok "couldn't create $MOUNT/$SPATH"
    echo "provision-vault: created $MOUNT/$SPATH with a new random key"
fi

# 5. credentials for the app
vault GET "auth/approle/role/$ROLE/role-id"; ok "couldn't read the role ID"
role_id="$(json_get 'd["data"]["role_id"]' <<<"$BODY")"
vault POST "auth/approle/role/$ROLE/secret-id" '{}'; ok "couldn't issue a secret ID"
secret_id="$(json_get 'd["data"]["secret_id"]' <<<"$BODY")"

umask 077
{
    echo "VAULT_ADDR=$ADDR"
    echo "VAULT_MOUNT=$MOUNT"
    echo "VAULT_SECRET_PATH=$SPATH"
    echo "VAULT_SECRET_FIELD=$FIELD"
    echo "VAULT_ROLE_ID=$role_id"
    echo "VAULT_SECRET_ID=$secret_id"
    [[ -z "${VAULT_NAMESPACE:-}" ]] || echo "VAULT_NAMESPACE=$VAULT_NAMESPACE"
    [[ -z "${VAULT_CACERT:-}" ]] || echo "#VAULT_CACERT=<path to the CA bundle, as the app sees it>"
} > "$OUT"
echo "provision-vault: AppRole '$ROLE' ready. Add the lines in $OUT to the app's .env (mode 0600), then delete the file."
