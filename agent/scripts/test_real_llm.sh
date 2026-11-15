#!/usr/bin/env bash
# Pinpoint --real-llm smoke test.
# Verifies every prerequisite in order and then runs one real incident.
# Usage: bash agent/scripts/test_real_llm.sh
set -u

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$REPO" || { echo "cannot cd to repo"; exit 1; }

# marker so stage 6 can tell a new incident folder from a stale one
SCRIPT_START_MARKER=$(mktemp)
trap 'rm -f "$SCRIPT_START_MARKER"' EXIT

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
RUNNER=()
if command -v uv > /dev/null; then
  pass "uv available (fastest path)"
  RUNNER=(uv run --python 3.11 --with-requirements agent/requirements.txt python3)
elif [ -n "$PY" ]; then
  if "$PY" -c "import deepagents, pydantic" 2>/dev/null; then
    pass "deepagents + pydantic already importable on system python"
    RUNNER=("$PY")
  else
    VENV="$REPO/.venv"
    if [ ! -d "$VENV" ]; then
      note "creating venv at .venv (PEP 668 safe)..."
      "$PY" -m venv "$VENV" && pass "venv created" || fail "venv creation failed"
    else
      pass "venv at .venv already exists"
    fi
    if [ -f "$VENV/bin/python3" ]; then
      note "installing requirements into .venv ..."
      "$VENV/bin/python3" -m pip install -q --upgrade pip > /dev/null 2>&1
      "$VENV/bin/python3" -m pip install -q -r agent/requirements.txt && \
        pass "deps installed into .venv" || fail "pip install into .venv failed"
      RUNNER=("$VENV/bin/python3")
    fi
  fi
fi
if [ "${#RUNNER[@]}" -eq 0 ]; then
  fail "no runner resolved"
fi

# ------ 5. The real run -----------------------------------------------
stage "5/6 running --real-llm"
if [ "$FAIL" -ne 0 ]; then
  note "skipping run; $FAIL prerequisite failure(s) above"
else
  RUN_INCIDENT_STARTED=1
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
if [ -z "${RUN_INCIDENT_STARTED:-}" ]; then
  note "skipping (stage 5 did not run)"
else
  # only look at an incident folder newer than when this script started
  # so stale folders from previous runs don't pollute the check
  LATEST=$(find agent/incidents -maxdepth 1 -mindepth 1 -type d -newer "$SCRIPT_START_MARKER" 2>/dev/null | sort | tail -1)
  if [ -z "$LATEST" ]; then
    fail "no new agent/incidents/<id>/ folder created by this run"
  else
    pass "incident created this run: $LATEST"
    MISSING=""
    for f in intake.json plan.md brief.json candidates.json location.json site.json route.json audit.json packet.md trace.jsonl events.jsonl; do
      [ -f "${LATEST}/${f}" ] || MISSING="$MISSING $f"
    done
    if [ -z "$MISSING" ]; then
      pass "all 11 expected artifacts present"
    else
      fail "missing artifacts:$MISSING"
    fi
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
