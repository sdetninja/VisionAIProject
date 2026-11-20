#!/usr/bin/env bash
# Local VisionGuard stack (architecture v3): M1 -> M2 -> M3 -> assessment service -> M6 gateway -> M7 dashboard,
# with the combined PPE + fire/smoke model and the ViT judges (milestone0/runs/judge, see ARCHITECTURE_V3_ASSESSMENT.md).
# Runs the same commands as visionguard/MANUAL_TESTING.md (section C), each in the background with a log file.
#
#   visionguard/scripts/dev_stack.sh start clip     # replay services/vision_pipeline/samples/combined_demo.mp4 (36 s)
#   visionguard/scripts/dev_stack.sh start webcam   # laptop webcam (macOS asks the terminal app for camera access)
#   visionguard/scripts/dev_stack.sh status         # which services answer on their ports
#   visionguard/scripts/dev_stack.sh token [role]   # dashboard login token (admin | reviewer | viewer), default admin
#   visionguard/scripts/dev_stack.sh logs <name>    # tail one log: m1 m2 m3 m3api m4 m6 m7
#   visionguard/scripts/dev_stack.sh stop
#   visionguard/scripts/dev_stack.sh reset          # stop, then move all runtime data to .dev/backup-<time> (fresh start)
#
# Fire/smoke LLM panel (Claude Opus 5.5 + Sonnet 5.5): export ANTHROPIC_API_KEY and JUDGE_PANEL=anthropic before start.
# Without it fire/smoke verdicts are UNSURE (a person decides). JUDGE_THRESHOLDS overrides the ViT judge file.
# Ports: M6 gateway 8000, M1 8001, M3 8003, assessment service (M4) 8004, dashboard 3000. Logs and PIDs: visionguard/.dev/
set -euo pipefail

VG="$(cd "$(dirname "$0")/.." && pwd)"
SVC="$VG/services"
DEV="$VG/.dev"
PY="$SVC/vision_pipeline/.venv/bin/python"        # this venv has every service's dependencies
LOGS="$DEV/logs"; PIDS="$DEV/pids"
export PYTHONUNBUFFERED=1                         # logs show each line as it happens (Python buffers when not on a terminal)
mkdir -p "$LOGS" "$PIDS"

ensure_env() {
  if [[ ! -f "$DEV/env.sh" ]]; then             # one random JWT secret per machine; tokens only work with this secret
    "$PY" -c "import secrets;print('export VG_JWT_SECRET='+secrets.token_urlsafe(48))" > "$DEV/env.sh"
    chmod 600 "$DEV/env.sh"
  fi
  # shellcheck disable=SC1091
  source "$DEV/env.sh"
}

run() {   # run <name> <dir> <command...>: background process, log to .dev/logs/<name>.log
  local name="$1" dir="$2"; shift 2
  if [[ -f "$PIDS/$name.pid" ]] && kill -0 "$(cat "$PIDS/$name.pid")" 2>/dev/null; then
    echo "  $name already running (pid $(cat "$PIDS/$name.pid"))"; return
  fi
  # background the command itself (not a subshell), so $! is the service PID and no shell keeps our stdout open
  pushd "$dir" > /dev/null
  nohup "$@" > "$LOGS/$name.log" 2>&1 < /dev/null &
  echo $! > "$PIDS/$name.pid"
  popd > /dev/null
  echo "  started $name (log: .dev/logs/$name.log)"
}

wait_port() {   # wait_port <port> <seconds>
  for _ in $(seq 1 "$2"); do curl -s -o /dev/null "http://127.0.0.1:$1/health" && return 0; sleep 1; done
  echo "  WARNING: nothing answering on port $1 yet (check the log)"; return 0
}

start() {
  local mode="${1:-clip}" m1cfg
  case "$mode" in
    clip)   m1cfg=configs/pipeline.combined.clip.yaml ;;
    webcam) m1cfg=configs/pipeline.combined.yaml ;;
    *) echo "mode must be clip or webcam"; exit 2 ;;
  esac
  ensure_env
  mkdir -p "$SVC/incident_service/data" "$SVC/backend_api/data"
  local judge="${JUDGE_THRESHOLDS:-$VG/../milestone0/runs/judge/thresholds.yaml}"
  [[ -f "$judge" ]] || echo "  NOTE: $judge not found: PPE incidents get NO_JUDGE (train the judges: milestone0 scripts 10-12)"
  echo "Starting VisionGuard ($mode)"
  # downstream first: every --follow reader waits for its input file, so nothing is missed when M1 starts
  run m2    "$SVC/rule_engine" "$PY" -m rule_engine --config configs/rules.combined.yaml \
              --input ../vision_pipeline/out/results/CAM_001.jsonl --output out/events.jsonl --follow
  run m3    "$SVC/incident_service" "$PY" -m incident_service --config configs/incident.dev.yaml \
              --input ../rule_engine/out/events.jsonl --follow
  run m3api "$SVC/incident_service" env INCIDENT_CONFIG=configs/incident.dev.yaml INCIDENT_OUTBOX=0 \
              "$PY" -m uvicorn incident_service.app:create_app --factory --port 8003
  # assessment service: one process (worker + small API on 8004), so the three ViT models are loaded once
  run m4    "$SVC/agent_service" "$PY" -m agent_service --bus ../incident_service/data/bus/events.jsonl \
              --m3-url http://127.0.0.1:8003 --kb knowledge_base --risk-config configs/risk.example.yaml \
              --judge "$judge" --panel "${JUDGE_PANEL:-none}" --llm "${LLM_KIND:-template}" --port 8004 --follow
  run m6   "$SVC/backend_api" "$PY" -m uvicorn backend_api.app:create_app --factory --port 8000
  run m7    "$VG/dashboard" npm run dev
  wait_port 8000 30
  # M1 last: the clip starts playing as soon as M1 is up
  run m1    "$SVC/vision_pipeline" env CONFIG_PATH="$m1cfg" "$PY" -m uvicorn vision_pipeline.app:app --port 8001
  wait_port 8001 60
  echo
  status
  echo
  echo "Dashboard: http://localhost:3000   (log in with: $0 token)"
  echo "M1 preview (no login, debug only): http://localhost:8001/cameras/CAM_001/stream"
}

stop() {
  for f in "$PIDS"/*.pid; do
    [[ -e "$f" ]] || continue
    pid="$(cat "$f")"; name="$(basename "$f" .pid)"
    if kill -0 "$pid" 2>/dev/null; then
      pkill -P "$pid" 2>/dev/null || true; kill "$pid" 2>/dev/null || true; echo "  stopped $name"
    fi
    rm -f "$f"
  done
  pkill -f "$VG/dashboard/node_modules" 2>/dev/null || true   # npm -> sh -> vite: make sure vite itself is gone
}

status() {
  for pair in "M6 gateway:8000" "M1 vision:8001" "M3 incidents:8003" "M4 assessment:8004"; do
    name="${pair%%:*}"; port="${pair##*:}"
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/health" || true)"
    printf "  %-14s :%s  %s\n" "$name" "$port" "$([[ "$code" == 200 ]] && echo up || echo "DOWN ($code)")"
  done
  code="$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3000 || true)"
  printf "  %-14s :%s  %s\n" "M7 dashboard" 3000 "$([[ "$code" == 200 ]] && echo up || echo "DOWN ($code)")"
  for w in m2 m3 m4; do
    if [[ -f "$PIDS/$w.pid" ]] && kill -0 "$(cat "$PIDS/$w.pid")" 2>/dev/null; then s=running; else s=stopped; fi
    printf "  %-14s       %s\n" "$w worker" "$s"
  done
}

case "${1:-}" in
  start)  start "${2:-clip}" ;;
  stop)   stop ;;
  status) status ;;
  token)  ensure_env; cd "$SVC/backend_api" && "$PY" -m backend_api token --sub "${3:-dev}" --role "${2:-admin}" ;;
  logs)   tail -f "$LOGS/${2:?name: m1 m2 m3 m3api m4 m6 m7}.log" ;;
  reset)
    stop
    b="$DEV/backup-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$b"
    for d in agent_service/data backend_api/data incident_service/data rule_engine/out vision_pipeline/out; do
      if [[ -e "$SVC/$d" ]]; then mkdir -p "$b/$(dirname "$d")"; mv "$SVC/$d" "$b/$d"; fi
    done
    echo "Runtime data moved to $b" ;;
  *) sed -n 2,13p "$0"; exit 2 ;;
esac
