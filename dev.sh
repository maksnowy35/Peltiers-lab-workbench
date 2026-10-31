#!/usr/bin/env bash
# teglab developer commands - bash on Linux, macOS and Git Bash on Windows.
#
#   ./dev.sh doctor      check Python, Node, npm, git and Docker versions
#   ./dev.sh setup       create .venv, install the backend (pinned) and the frontend
#   ./dev.sh api         backend on http://127.0.0.1:8000 with reload (API docs at /docs)
#   ./dev.sh ui          frontend on http://localhost:4200 (proxies /api to :8000)
#   ./dev.sh test [...]  backend test suite (extra arguments go to pytest, e.g. -k nodal)
#   ./dev.sh lint        ruff check + format check (everything CI lints)
#   ./dev.sh fix         ruff --fix + format
#   ./dev.sh contract    regenerate frontend/src/app/core/pipeline.models.ts from the Pydantic models
#   ./dev.sh build       production build of the UI (frontend/dist)
#   ./dev.sh serve       backend serving the built UI on http://127.0.0.1:8000 (like the container)
#   ./dev.sh e2e-setup   install Playwright and its Chromium for the browser tests
#   ./dev.sh e2e [...]   browser smoke tests against a running app (TEGLAB_URL, default :8000)
#   ./dev.sh check       lint + tests + contract + UI build - what CI runs before a push
#   ./dev.sh docker      docker compose up --build
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [ -d "$ROOT/.venv/Scripts" ]; then
  VENV_BIN="$ROOT/.venv/Scripts"   # Windows
else
  VENV_BIN="$ROOT/.venv/bin"
fi
PY="$VENV_BIN/python"

# Windows Python does not understand Git Bash paths like /c/Users/...; cygpath turns them into C:/Users/...
native_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi; }

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# A Python >= 3.11 that really runs (on Windows "python3" can be the Microsoft Store stub).
base_python() {
  local candidates=("py -3.13" "py -3.12" "py -3.11" "python3.13" "python3.12" "python3.11" "python3" "python")
  local c
  for c in "${candidates[@]}"; do
    # shellcheck disable=SC2086
    if $c -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

need_venv() {
  [ -x "$PY" ] || [ -x "$PY.exe" ] || die "no .venv yet - run ./dev.sh setup first"
}

cmd_doctor() {
  local bp
  bp="$(base_python || true)"
  echo "python (for the venv): ${bp:-NOT FOUND - install Python 3.12 or 3.13}"
  [ -n "$bp" ] && $bp --version
  if [ -x "$PY" ] || [ -x "$PY.exe" ]; then echo ".venv: $("$PY" --version)"; else echo ".venv: not created"; fi
  if command -v node >/dev/null; then
    echo "node: $(node --version)  (Angular 22 needs ^22.22.3 or ^24.15.0 - see .nvmrc)"
  else
    echo "node: NOT FOUND - install Node.js 24 LTS"
  fi
  command -v npm >/dev/null && echo "npm: $(npm --version)"
  command -v git >/dev/null && echo "git: $(git --version)"
  if command -v docker >/dev/null; then echo "docker: $(docker --version)"; else echo "docker: not found (optional)"; fi
}

cmd_setup() {
  local bp
  bp="$(base_python)" || die "Python 3.11+ not found - install Python 3.13 from python.org"
  if [ ! -x "$PY" ] && [ ! -x "$PY.exe" ]; then
    say "creating .venv with $($bp --version)"
    $bp -m venv "$ROOT/.venv"
  fi
  say "installing the backend with the pinned versions (backend/constraints.txt)"
  "$PY" -m pip install --upgrade pip
  (cd backend && "$PY" -m pip install -e ".[dev]" -c constraints.txt)
  command -v npm >/dev/null || die "npm not found - install Node.js 24 LTS, then run ./dev.sh setup again"
  say "installing the frontend"
  if [ -f frontend/package-lock.json ]; then
    (cd frontend && npm ci --no-audit --no-fund)
  else
    (cd frontend && npm install --no-audit --no-fund)
    say "created frontend/package-lock.json - commit it, it freezes every npm version"
  fi
  say "done - next: ./dev.sh test, then ./dev.sh api and ./dev.sh ui in two terminals"
}

cmd_api() {
  need_venv
  cd backend && exec "$PY" -m teglab --reload "$@"
}

cmd_ui() {
  [ -d frontend/node_modules ] || die "frontend not installed - run ./dev.sh setup"
  cd frontend && exec npm start -- "$@"
}

cmd_test() {
  need_venv
  (cd backend && "$PY" -m pytest "$@")
}

cmd_lint() {
  need_venv
  "$PY" -m ruff check .
  "$PY" -m ruff format --check .
}

cmd_fix() {
  need_venv
  "$PY" -m ruff check --fix .
  "$PY" -m ruff format .
}

cmd_contract() {
  need_venv
  [ -f backend/scripts/gen_ts_types.py ] || die "backend/scripts/gen_ts_types.py not in this checkout yet"
  "$PY" backend/scripts/gen_ts_types.py "$@"
}

cmd_build() {
  [ -d frontend/node_modules ] || die "frontend not installed - run ./dev.sh setup"
  (cd frontend && npm run build)
}

cmd_serve() {
  need_venv
  local ui="$ROOT/frontend/dist/teglab-ui/browser"
  [ -f "$ui/index.html" ] || cmd_build
  cd backend && exec "$PY" -m teglab --static "$(native_path "$ui")" "$@"
}

cmd_e2e_setup() {
  need_venv
  "$PY" -m pip install -r e2e/requirements.txt
  "$PY" -m playwright install chromium
}

cmd_e2e() {
  need_venv
  [ -d e2e ] || die "e2e/ not in this checkout yet"
  "$PY" -c 'import playwright' 2>/dev/null || die "Playwright missing - run ./dev.sh e2e-setup"
  "$PY" -m pytest e2e "$@"
}

cmd_check() {
  say "lint";  cmd_lint
  say "tests"; cmd_test
  if [ -f backend/scripts/gen_ts_types.py ]; then say "API contract"; cmd_contract --check; fi
  say "UI production build"; cmd_build
  say "all checks passed"
}

cmd_docker() {
  command -v docker >/dev/null || die "docker not found - install Docker Desktop"
  exec docker compose up --build "$@"
}

usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; }

case "${1:-help}" in
  doctor) shift; cmd_doctor "$@" ;;
  setup) shift; cmd_setup "$@" ;;
  api) shift; cmd_api "$@" ;;
  ui) shift; cmd_ui "$@" ;;
  test) shift; cmd_test "$@" ;;
  lint) shift; cmd_lint "$@" ;;
  fix) shift; cmd_fix "$@" ;;
  contract) shift; cmd_contract "$@" ;;
  build) shift; cmd_build "$@" ;;
  serve) shift; cmd_serve "$@" ;;
  e2e-setup) shift; cmd_e2e_setup "$@" ;;
  e2e) shift; cmd_e2e "$@" ;;
  check) shift; cmd_check "$@" ;;
  docker) shift; cmd_docker "$@" ;;
  help | -h | --help) usage ;;
  *) usage; exit 1 ;;
esac
