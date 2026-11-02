#!/usr/bin/env bash
# The FelCloud slot, in order (Tuesday 2026-11-03, 12:00-24:00). Every step writes to reports/felcloud-<date>-*; the
# sequence stops at the first step that shows a server 429 or an exception (or that died without writing its report).
#
#   scripts/slot_run.sh                       # date 2026-11-03, ENV_FILE=.env.real
#   SLOT_DATE=2026-11-04 scripts/slot_run.sh  # another day: the file names follow
#
#   a. smoke of every task, including one OCR call      -> -smoke.log, -smoke.guard.json
#   b. the 60-claim eval, twice: the compact class judges each claim part (CLAIM_VERDICT_CLASS=compact), then mid-size
#                                                       -> -check-claim-compact.*, -check-claim-midsize.* (json, log, guard.json)
#   c. load_mixed --real --no-cache (240 s), every per-request record saved
#                                                       -> -load-mixed.{json,md,log}, -load-mixed.records.jsonl
#      then the share of interactive explain degraded by `deadline`  -> -explain-deadline.{json,md}
#   then a scan of everything written for key-looking strings.
# (The vision-window probe was answered on 2026-11-02: a vision request uses the 20 / min window.)
#
# Steps are separate processes, each with its own engine limiter, but the platform's 20-per-minute window is shared:
# the script waits SLOT_GAP_S (default 65 s) between steps so one step's sends have left the window before the next
# starts. Environment: ENV_FILE (default .env.real), PYTHON (default myenv/bin/python, else python), SLOT_DATE,
# SLOT_GAP_S. Never start Ollama for this; it is FelCloud only.
set -u -o pipefail
cd "$(dirname "$0")/.."

ENV_FILE=${ENV_FILE:-.env.real}; export ENV_FILE
if [ -n "${PYTHON:-}" ]; then PY=$PYTHON; elif [ -x myenv/bin/python ]; then PY=myenv/bin/python; else PY=python; fi
DATE=${SLOT_DATE:-2026-11-03}
GAP=${SLOT_GAP_S:-65}
P=reports/felcloud-$DATE
mkdir -p reports

scan_reports() {
  local files=()
  for f in "$P"-*; do [ -f "$f" ] && files+=("$f"); done
  [ ${#files[@]} -gt 0 ] && "$PY" -m scripts.slot_guard scan --env-file "$ENV_FILE" "${files[@]}"
}

halt() {   # halt <step> <why>
  echo
  echo "=== SEQUENCE STOPPED at step $1: $2"
  echo "=== later steps were not run; what was written so far is in $P-*"
  scan_reports
  exit 1
}

pause() {
  [ "$GAP" -gt 0 ] && { echo "-- waiting ${GAP}s so the platform's 60 s window empties"; sleep "$GAP"; }
  return 0
}

begin() {   # begin <letter> <title> <files to clear...>: a stale report must never pass for this step's
  echo; echo "=== $1. $2"; shift 2; rm -f "$@"
}

# ---- preflight
[ -f "$ENV_FILE" ] || { echo "no $ENV_FILE: this runs against FelCloud and needs its key profile"; exit 2; }
"$PY" - <<'EOF' || { echo "$ENV_FILE simulates the constraints (SIMULATE_CONSTRAINTS): not a live profile"; exit 2; }
import sys
from config import load_settings
sys.exit(1 if load_settings().simulate else 0)
EOF
[ "$(date +%u)" -ge 6 ] && echo "WARNING: today is a weekend; the FelCloud slots are on weekdays."
echo "slot run $DATE -> $P-*   (gap between steps: ${GAP}s)"

# ---- a. smoke of every task, one OCR call included
begin a "smoke: every task, one OCR call" "$P-smoke.log" "$P-smoke.guard.json"
"$PY" -m scripts.smoke --no-cache --guard-out "$P-smoke.guard.json" 2>&1 | tee "$P-smoke.log"
"$PY" -m scripts.slot_guard check "$P-smoke.guard.json" || halt a "smoke: a server 429 or an exception"
pause

# ---- b. the 60-claim eval with the compact class, then again with mid-size judging each claim part
for CLS in compact midsize; do
  [ "$CLS" = midsize ] && STEP=b2 || STEP=b1
  begin $STEP "eval_check_claim, CLAIM_VERDICT_CLASS=$CLS" "$P-check-claim-$CLS.json" "$P-check-claim-$CLS.guard.json" "$P-check-claim-$CLS.log"
  CLAIM_VERDICT_CLASS=$CLS "$PY" -m scripts.eval_check_claim --out "$P-check-claim-$CLS.json" \
      --guard-out "$P-check-claim-$CLS.guard.json" 2>&1 | tee "$P-check-claim-$CLS.log"
  "$PY" -m scripts.slot_guard check "$P-check-claim-$CLS.guard.json" || halt $STEP "eval_check_claim ($CLS): a server 429 or an exception"
  pause
done

# ---- c. the mixed load on the real API, every request reaching the provider, every per-request record saved
begin c "load_mixed --real --no-cache" "$P-load-mixed.json" "$P-load-mixed.md" "$P-load-mixed.log" "$P-load-mixed.records.jsonl"
"$PY" -m scripts.load_mixed --real --no-cache --out "$P-load-mixed" 2>&1 | tee "$P-load-mixed.log"
# load_mixed exits 1 when ANY of its verdicts fails (a p95 over 25 s is a finding, not a reason to stop): only a server
# 429 or an exception, or a missing report, stops the sequence.
"$PY" -m scripts.slot_guard check "$P-load-mixed.json" || halt c "load_mixed: a server 429 or an exception"
[ -s "$P-load-mixed.records.jsonl" ] || halt c "load_mixed wrote no per-request records"

# ---- e. the share of explain results degraded by `deadline`
begin c2 "explain degraded by deadline" "$P-explain-deadline.json" "$P-explain-deadline.md"
"$PY" -m scripts.explain_deadline "$P-load-mixed.json" --out "$P-explain-deadline" || halt c2 "no explain share could be computed"

# ---- the reports are meant to be committed: nothing key-like in them
echo; echo "=== secret scan of $P-*"
scan_reports || halt scan "a key-looking string in the reports: remove it before committing anything"
echo; echo "=== slot run complete: $(ls "$P"-* | wc -l) files in reports/"
