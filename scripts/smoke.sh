#!/usr/bin/env bash
set -euo pipefail

SMOKE_DIR="$(mktemp -d)"
trap 'rm -rf "$SMOKE_DIR"' EXIT

cd "$SMOKE_DIR"
echo "[smoke] working in $SMOKE_DIR"

git init
git config user.email "smoke@example.com"
git config user.name "Smoke Test"
echo "# smoke" > README.md
git add .
git commit -m "init"

PLAN="$SMOKE_DIR/plan.md"
cat > "$PLAN" <<'EOF'
---
scope: ["*"]
acceptance:
  - hello.txt exists with content sub-agy-smoke
---
Create hello.txt containing exactly "sub-agy-smoke", verify it, then commit with a conventional commit message.
EOF

echo "[smoke] sub-agy run ..."
RUN_OUT=$(sub-agy --cwd "$SMOKE_DIR" run --plan "$PLAN" --auto-approve --effort low --timeout 5m)
JOB_ID=$(echo "$RUN_OUT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["job_id"])')
echo "[smoke] job $JOB_ID"

for i in {1..60}; do
  STATE=$(sub-agy --cwd "$SMOKE_DIR" status "$JOB_ID" | python3 -c 'import sys,json; print(json.load(sys.stdin)["state"])')
  echo "[smoke] state: $STATE"
  if [[ "$STATE" == "done" || "$STATE" == "error" || "$STATE" == "cancelled" || "$STATE" == "interrupted" ]]; then
    break
  fi
  sleep 5
done

if [[ "$STATE" != "done" ]]; then
  echo "[smoke] FAILED: job did not finish successfully (state=$STATE)"
  echo "--- meta.json ---"
  cat "$SMOKE_DIR/.subagy/jobs/$JOB_ID/meta.json" || true
  echo "--- events.ndjson tail ---"
  tail -n 20 "$SMOKE_DIR/.subagy/jobs/$JOB_ID/events.ndjson" || true
  echo "--- stderr.log tail ---"
  tail -n 50 "$SMOKE_DIR/.subagy/jobs/$JOB_ID/stderr.log" || true
  exit 1
fi

echo "[smoke] sub-agy result ..."
RESULT=$(sub-agy --cwd "$SMOKE_DIR" result "$JOB_ID")
echo "$RESULT"
python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('contract_ok') is True, 'contract_ok false'; print('[smoke] contract_ok=true')" <<EOF
$RESULT
EOF

# Verify the file and commit
WORKTREE=$(echo "$RUN_OUT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["worktree"])')
if [[ ! -f "$WORKTREE/hello.txt" ]]; then
  echo "[smoke] FAILED: hello.txt missing in $WORKTREE"
  exit 1
fi
CONTENT=$(cat "$WORKTREE/hello.txt")
if [[ "$CONTENT" != "sub-agy-smoke" ]]; then
  echo "[smoke] FAILED: hello.txt content is '$CONTENT', expected 'sub-agy-smoke'"
  exit 1
fi
echo "[smoke] hello.txt content verified"

# Feedback round
echo "[smoke] sub-agy feedback ..."
sub-agy --cwd "$SMOKE_DIR" feedback "$JOB_ID" "Change hello.txt content to sub-agy-smoke-2 and amend the commit."

for i in {1..60}; do
  STATE=$(sub-agy --cwd "$SMOKE_DIR" status "$JOB_ID" | python3 -c 'import sys,json; print(json.load(sys.stdin)["state"])')
  echo "[smoke] feedback state: $STATE"
  if [[ "$STATE" == "done" || "$STATE" == "error" || "$STATE" == "cancelled" || "$STATE" == "interrupted" ]]; then
    break
  fi
  sleep 5
done

if [[ "$STATE" != "done" ]]; then
  echo "[smoke] FAILED: feedback round did not finish (state=$STATE)"
  cat "$SMOKE_DIR/.subagy/jobs/$JOB_ID/meta.json" || true
  tail -n 20 "$SMOKE_DIR/.subagy/jobs/$JOB_ID/events.ndjson" || true
  tail -n 50 "$SMOKE_DIR/.subagy/jobs/$JOB_ID/stderr.log" || true
  exit 1
fi

CONTENT2=$(cat "$WORKTREE/hello.txt")
if [[ "$CONTENT2" != "sub-agy-smoke-2" ]]; then
  echo "[smoke] FAILED: after feedback hello.txt content is '$CONTENT2', expected 'sub-agy-smoke-2'"
  exit 1
fi
echo "[smoke] feedback content verified"

# Cleanup
echo "[smoke] sub-agy cleanup ..."
sub-agy --cwd "$SMOKE_DIR" cleanup "$JOB_ID" --purge --delete-branch

echo "[smoke] PASSED"
