#!/bin/sh
# Smoke check of a live PartnerType instance's API gateway (/mcp, OAuth
# discovery, /api/v1) with curl. Read-only: it creates, changes and deletes
# nothing on the instance.
#
#   PARTNERTYPE_KEY=ptk_... scripts/mcp-smoke.sh https://crm.example.com
#
# The key comes ONLY from the PARTNERTYPE_KEY environment variable, never
# from the command line (argv is visible in the process list and the shell
# history). It is written to a mode-600 header file in a private temporary
# directory, handed to curl with `-H @file`, and removed on exit. It is never
# printed.
#
# Options (environment):
#   SMOKE_INSECURE_TLS=1   accept the self-signed certificate of a local
#                          `tls internal` instance (curl -k). Never use it
#                          against a public address.
#
# Output: one `PASS <step>` or `FAIL <step> (got <status>)` line per step.
# Exit code: 0 when every step passed, 1 on the first failed step, 2 on a
# usage error (missing key, key given as an argument, bad address).
set -eu

usage() {
    echo "Usage: PARTNERTYPE_KEY=ptk_… $0 <instance-address>" >&2
    echo "  e.g.  PARTNERTYPE_KEY=ptk_… $0 https://crm.example.com" >&2
}

if [ "$#" -ne 1 ]; then
    case "$*" in
        *ptk_*|*Bearer*|*bearer*)
            echo "Refused: the key cannot be an argument. Put it in the PARTNERTYPE_KEY variable." >&2 ;;
    esac
    usage
    exit 2
fi

case "$1" in
    *ptk_*|*Bearer*|*bearer*)
        echo "Refused: the key cannot be an argument. Put it in the PARTNERTYPE_KEY variable." >&2
        exit 2 ;;
    https://*) ;;
    http://localhost|http://localhost:*|http://localhost/*|\
    http://127.0.0.1|http://127.0.0.1:*|http://127.0.0.1/*|\
    http://\[::1\]|http://\[::1\]:*|http://\[::1\]/*) ;;
    http://*)
        echo "Refused: plain http:// only for localhost. Otherwise the key would travel as clear text." >&2
        exit 2 ;;
    *)
        usage
        exit 2 ;;
esac
BASE=${1%/}

if [ -z "${PARTNERTYPE_KEY:-}" ]; then
    echo "Refused: no key. Set the PARTNERTYPE_KEY variable (a key from Settings › Integrations)." >&2
    usage
    exit 2
fi
case "$PARTNERTYPE_KEY" in
    *[!A-Za-z0-9._~+/=-]*)
        echo "Refused: PARTNERTYPE_KEY contains characters that are not allowed (space, newline…)." >&2
        exit 2 ;;
esac

INSECURE=""
if [ "${SMOKE_INSECURE_TLS:-}" = "1" ]; then
    INSECURE="-k"
fi

umask 077
WORK=$(mktemp -d "${TMPDIR:-/tmp}/mcp-smoke.XXXXXX")
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

AUTH="$WORK/auth-header"
BODY="$WORK/body"
HEAD="$WORK/head"
# printf is a shell builtin: the key never appears in the process list.
printf 'Authorization: Bearer %s\n' "$PARTNERTYPE_KEY" > "$AUTH"
chmod 600 "$AUTH"

MCP_VERSION="2025-11-25"
STATUS=""

# request <curl arguments...> -> sets STATUS, fills $BODY and $HEAD
request() {
    : > "$BODY"
    : > "$HEAD"
    STATUS=$(curl -sS $INSECURE --max-time 20 -o "$BODY" -D "$HEAD" \
        -w '%{http_code}' "$@") || true
    [ -n "$STATUS" ] || STATUS="000"
}

pass() { echo "PASS $1"; }
fail() { echo "FAIL $1 (got $2)"; exit 1; }

# expect <step> <status> [body-pattern] [head-pattern]
expect() {
    step=$1
    want=$2
    body_pattern=${3:-}
    head_pattern=${4:-}
    [ "$STATUS" = "$want" ] || fail "$step" "$STATUS"
    if [ -n "$body_pattern" ] && ! grep -Eq -e "$body_pattern" "$BODY"; then
        fail "$step" "$STATUS, missing from the body: $body_pattern"
    fi
    if [ -n "$head_pattern" ] && ! grep -Eiq -e "$head_pattern" "$HEAD"; then
        fail "$step" "$STATUS, missing from the headers: $head_pattern"
    fi
    pass "$step"
}

# mcp <json-rpc message> [extra curl arguments...]  (with the key)
mcp() {
    message=$1
    shift
    request -X POST "$BASE/mcp" -H @"$AUTH" \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' \
        "$@" --data "$message"
}

request "$BASE/health"
expect "health" 200

request "$BASE/.well-known/oauth-protected-resource/mcp"
expect "discovery: protected resource (PRM)" 200 '"resource"'

request "$BASE/.well-known/oauth-authorization-server"
expect "discovery: authorization server" 200 '"token_endpoint"'

request -X POST "$BASE/mcp" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"mcp-smoke","version":"0"}}}'
expect "POST /mcp without a key -> 401 + resource_metadata" 401 "" '^www-authenticate:.*resource_metadata='

mcp '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"mcp-smoke","version":"0"}}}'
expect "mcp: initialize" 200 '"protocolVersion"'

mcp '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' -H "MCP-Protocol-Version: $MCP_VERSION"
expect "mcp: tools/list" 200 '"list_leads"'

mcp '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_leads","arguments":{"per_page":1}}}' \
    -H "MCP-Protocol-Version: $MCP_VERSION"
expect "mcp: tools/call list_leads" 200 '"isError": ?false'

request -X GET "$BASE/mcp" -H @"$AUTH"
expect "GET /mcp -> 405" 405

mcp '{"jsonrpc":"2.0","id":4,"method":"tools/list"}' -H 'MCP-Protocol-Version: 2026-07-28'
expect "unsupported protocol version -> 400" 400 '-32600'

request "$BASE/api/v1/leads?per_page=1" -H @"$AUTH"
expect "GET /api/v1/leads" 200 '"untrusted_data"'

request "$BASE/api/v1/openapi.json"
expect "GET /api/v1/openapi.json" 200 '"openapi"'

echo "All steps: PASS"
