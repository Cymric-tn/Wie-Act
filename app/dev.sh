#!/usr/bin/env bash
# Starts the whole app for development: the engine bridge, the Node server and the client.
#
#   app/dev.sh            # the engine's FakeEngine: no key, no model, no quota (the default)
#   app/dev.sh --real     # the real engine, ENV_FILE=.env.real (spends the team's FelCloud quota)
#   app/dev.sh --stop     # nothing to do: Ctrl-C stops all three
#
# Ports (one place; the README lists the same): override with BRIDGE_PORT, SERVER_PORT, CLIENT_PORT.
# Needs Node >= 22.5 (the server uses node:sqlite) and a python with the engine's requirements (ENGINE_PYTHON, else
# ../myenv/bin/python, else python3). A local ENGINE_SERVICE_TOKEN is generated when none is set.
set -u -o pipefail
cd "$(dirname "$0")"
ROOT=$(cd .. && pwd)

BRIDGE_PORT=${BRIDGE_PORT:-8765}
SERVER_PORT=${SERVER_PORT:-3000}
CLIENT_PORT=${CLIENT_PORT:-5173}
MODE=fake
for a in "$@"; do
  case "$a" in
    --real) MODE=real ;;
    --fake) MODE=fake ;;
    -h|--help) sed -n 2,12p "$0"; exit 0 ;;
    *) echo "unknown option $a (try --help)"; exit 2 ;;
  esac
done

# ---- Node with node:sqlite
node_ok() { "$1" -e "require('node:sqlite')" >/dev/null 2>&1; }
NODE=$(command -v node || true)
if [ -z "$NODE" ] || ! node_ok "$NODE"; then
  for c in "$HOME"/.nvm/versions/node/v2[2-9]*/bin/node "$HOME"/.nvm/versions/node/v[3-9][0-9]*/bin/node; do
    [ -x "$c" ] && node_ok "$c" && NODE=$c && break
  done
fi
if [ -z "$NODE" ] || ! node_ok "$NODE"; then
  echo "Node 22.5 or newer is required (node:sqlite). Install it, e.g. 'nvm install 24 && nvm use 24'."; exit 1
fi
export PATH="$(dirname "$NODE"):$PATH"

# ---- python with the engine's requirements
PY=${ENGINE_PYTHON:-}
[ -z "$PY" ] && [ -x "$ROOT/myenv/bin/python" ] && PY=$ROOT/myenv/bin/python
[ -z "$PY" ] && PY=python3
if ! (cd "$ROOT" && "$PY" -c "import engine.fake, pydantic, yaml, sympy" >/dev/null 2>&1); then
  echo "$PY cannot import the engine: install its requirements ('pip install -r requirements.txt' at the repo root) or set ENGINE_PYTHON."; exit 1
fi

# ---- ports must be free
for p in "$BRIDGE_PORT" "$SERVER_PORT" "$CLIENT_PORT"; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then
    echo "port $p is already in use (set BRIDGE_PORT / SERVER_PORT / CLIENT_PORT, or stop what runs there)."; exit 1
  fi
done

# ---- secret shared by the bridge and the server
if [ -z "${ENGINE_SERVICE_TOKEN:-}" ]; then
  ENGINE_SERVICE_TOKEN=dev-$(head -c 18 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 24)
fi
export ENGINE_SERVICE_TOKEN

# ---- dependencies
for d in server client; do
  [ -d "$d/node_modules" ] || { echo "installing $d dependencies..."; (cd "$d" && npm install --no-audit --no-fund >/dev/null) || exit 1; }
done

mkdir -p .dev-logs
PIDS=()
cleanup() { trap - INT TERM EXIT; echo; echo "stopping..."; for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done; wait 2>/dev/null; }
trap cleanup INT TERM EXIT

# ---- the bridge
if [ "$MODE" = real ]; then
  [ -f "$ROOT/.env.real" ] || { echo "no .env.real at the repo root: the real engine needs its key profile."; exit 1; }
  echo "REAL engine: every AI request spends the team's FelCloud quota (20 / min)."
  ENV_FILE="$ROOT/.env.real" "$PY" bridge/bridge.py --port "$BRIDGE_PORT" >.dev-logs/bridge.log 2>&1 &
else
  "$PY" bridge/bridge.py --fake --fake-speed "${FAKE_SPEED:-0.2}" --port "$BRIDGE_PORT" >.dev-logs/bridge.log 2>&1 &
fi
PIDS+=($!)

# ---- the Node server, then the client
(cd server && PORT="$SERVER_PORT" ENGINE_URL="http://127.0.0.1:$BRIDGE_PORT" \
  CLIENT_ORIGINS="http://localhost:$CLIENT_PORT,http://127.0.0.1:$CLIENT_PORT" \
  exec "$NODE" --disable-warning=ExperimentalWarning index.js) >.dev-logs/server.log 2>&1 &
PIDS+=($!)
(cd client && VITE_SERVER_URL="http://127.0.0.1:$SERVER_PORT" exec ./node_modules/.bin/vite --host 127.0.0.1 --port "$CLIENT_PORT" --strictPort) >.dev-logs/client.log 2>&1 &
PIDS+=($!)

# ---- wait until the engine answers through the server
for _ in $(seq 1 60); do
  health=$(curl -s "http://127.0.0.1:$SERVER_PORT/health" 2>/dev/null || true)
  case "$health" in *'"connected":true'*) break ;; esac
  for p in "${PIDS[@]}"; do kill -0 "$p" 2>/dev/null || { echo "a process exited: see app/.dev-logs/"; tail -n 5 .dev-logs/*.log; exit 1; }; done
  sleep 0.5
done
case "$health" in *'"connected":true'*) ;; *) echo "the app did not come up: see app/.dev-logs/"; exit 1 ;; esac

echo
echo "  app        http://127.0.0.1:$CLIENT_PORT"
echo "  server     http://127.0.0.1:$SERVER_PORT   (health: /health)"
echo "  engine     http://127.0.0.1:$BRIDGE_PORT   ($MODE; logs in app/.dev-logs/)"
[ "$MODE" = fake ] && echo "  force a state: send context.fake_outcome (see app/HANDOFF.md), e.g. quota_exhausted, deadline, wrong_math"
echo "  Ctrl-C stops everything."
wait
