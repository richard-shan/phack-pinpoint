#!/usr/bin/env bash
# Pinpoint --real-llm smoke test.
# Verifies every prerequisite in order and then runs one real incident.
# Usage: bash agent/scripts/test_real_llm.sh
set -u

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$REPO" || { echo "cannot cd to repo"; exit 1; }

FAIL=0
pass() { printf "\033[32m  pass\033[0m  %s\n" "$1"; }
fail() { printf "\033[31m  fail\033[0m  %s\n" "$1"; FAIL=$((FAIL+1)); }
note() { printf "        %s\n" "$1"; }
stage(){ echo; printf "\033[1m[%s]\033[0m\n" "$1"; }

echo "Pinpoint real-llm smoke test"
echo "repo: $REPO"

# ------ 1. Python 3.11+ -----------------------------------------------
stage "1/6 python 3.11+"
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" > /dev/null; then
    V=$("$c" --version 2>&1 | awk '{print $2}')
    MAJ=$(echo "$V" | cut -d. -f1); MIN=$(echo "$V" | cut -d. -f2)
    if [ "$MAJ" = "3" ] && [ "$MIN" -ge 11 ]; then
      PY=$(command -v "$c")
      pass "$c ($V)"
      break
    fi
  fi
done
if [ -z "$PY" ]; then
  if command -v uv > /dev/null; then
    pass "uv available, will use uv-managed 3.11"
    PY="UV"
  else
    fail "no python >= 3.11 found; install with brew or uv python install 3.11"
  fi
fi

# ------ 2. .env.local + key -------------------------------------------
stage "2/6 .env.local"
if [ ! -f "$REPO/.env.local" ]; then
  fail ".env.local missing at $REPO"
else
  pass ".env.local present"
  KEY=$(grep -E '^(export )?ANTHROPIC_API_KEY' "$REPO/.env.local" | head -1 | sed -E 's/.*=["'"'"']?([^"'"'"'[:space:]]+)["'"'"']?/\1/')
  if [ -z "${KEY:-}" ]; then
    fail "no ANTHROPIC_API_KEY line found"
  elif [[ "$KEY" != sk-ant-* ]]; then
    fail "key present but prefix is not sk-ant-"
  else
    pass "key extracted (length ${#KEY}, prefix sk-ant-)"
  fi
fi

# ------ 3. Direct API auth probe --------------------------------------
stage "3/6 Anthropic auth"
if [ -n "${KEY:-}" ]; then
  RESP=$(curl -sS -w "\n__HTTP__%{http_code}" \
    -H "x-api-key: $KEY" \
    -H "anthropic-version: 2023-06-01" \
    -H "content-type: application/json" \
    -d '{"model":"claude-haiku-5-5","max_tokens":5,"messages":[{"role":"user","content":"hi"}]}' \
    https://api.anthropic.com/v1/messages 2>&1)
  HTTP=$(echo "$RESP" | grep -oE '__HTTP__[0-9]+' | cut -d_ -f5)
  if [ "$HTTP" = "200" ]; then
    pass "/v1/messages HTTP 200"
  else
    fail "/v1/messages HTTP ${HTTP:-??}"
    note "body: $(echo "$RESP" | sed 's/__HTTP__.*//' | head -c 200)"
    note "fix: regenerate key at console.anthropic.com -> API Keys"
  fi
fi

# ------ 4. Deps available or installable ------------------------------
stage "4/6 python deps"
if [ "$PY" = "UV" ]; then
  RUNNER=(uv run --python 3.11 --with-requirements agent/requirements.txt python3)
  pass "using uv-managed env"
else
  if "$PY" -c "import deepagents, pydantic" 2>/dev/null; then
    pass "deepagents + pydantic importable"
  else
    note "installing from agent/requirements.txt (one-time)..."
    "$PY" -m pip install -q -r agent/requirements.txt && \
      pass "install succeeded" || fail "pip install failed"
  fi
  RUNNER=("$PY")
fi

# ------ 5. The real run -----------------------------------------------
stage "5/6 running --real-llm"
if [ "$FAIL" -ne 0 ]; then
  note "skipping run; $FAIL prerequisite failure(s) above"
else
  OUT=$(mktemp)
  "${RUNNER[@]}" agent/run_incident.py --real-llm > "$OUT" 2>&1
  RC=$?
  echo "    --- last 30 lines of run output ---"
  tail -30 "$OUT" | sed 's/^/    /'
  echo "    --- end of output ---"
  if [ "$RC" -eq 0 ]; then
    pass "run_incident exited 0"
  else
    fail "run_incident exited $RC"
  fi
  COSTLINE=$(grep '\[budget\]' "$OUT" | tail -1)
  if [ -n "$COSTLINE" ]; then
    pass "budget: $COSTLINE"
  else
    fail "no [budget] line in output (run may have died before completion)"
  fi
  rm -f "$OUT"
fi

# ------ 6. Incident folder artifacts ----------------------------------
stage "6/6 incident artifacts"
LATEST=$(ls -td agent/incidents/*/ 2>/dev/null | head -1)
if [ -z "$LATEST" ]; then
  fail "no agent/incidents/<id>/ folder found"
else
  pass "latest incident: $LATEST"
  MISSING=""
  for f in intake.json plan.md brief.json candidates.json location.json site.json route.json audit.json packet.md trace.jsonl events.jsonl; do
    [ -f "${LATEST}${f}" ] || MISSING="$MISSING $f"
  done
  if [ -z "$MISSING" ]; then
    pass "all 11 expected artifacts present"
  else
    fail "missing artifacts:$MISSING"
  fi
fi

echo
if [ "$FAIL" -eq 0 ]; then
  printf "\033[1;32mall checks passed\033[0m\n"
  exit 0
else
  printf "\033[1;31m%s check(s) failed\033[0m\n" "$FAIL"
  exit 1
fi
