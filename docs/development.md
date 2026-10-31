# Development setup

Everything runs from a bash terminal - Git Bash on Windows, any shell on Linux and macOS - through one
script, `dev.sh`, which VS Code tasks and CI use as well. The only Windows-specific step is installing the
tools below.

## 1 · Requirements

| Tool | Version | Notes |
| --- | --- | --- |
| Python | **3.13** (3.11-3.13 supported, CI tests all three) | Windows: installer from python.org, tick *Add python.exe to PATH*. `dev.sh` finds it through the `py` launcher. |
| Node.js | **24 LTS**, at least 24.15 (or 22.22.3+) | Angular 22 refuses older versions. `.nvmrc` says `24` for nvm, nvm-windows or fnm. npm comes with it. |
| Git | any recent | Windows: Git for Windows - it provides **Git Bash**. |
| VS Code | any recent | recommended extensions are listed in `.vscode/extensions.json`. |
| Docker Desktop | optional | only for `./dev.sh docker`; the container builds everything itself. |
| ANSYS Mechanical | optional | only to export your own nodal results (`tools/ansys`). |

Check what you have with `./dev.sh doctor`.

## 2 · Library versions

The backend is installed with **exact pins** from `backend/constraints.txt` - the versions the test suite
passed with - in your venv, in CI and in the Docker image alike:

| Package | Pinned | Allowed by `pyproject.toml` |
| --- | --- | --- |
| fastapi / starlette | 0.141.1 / 1.0.0 | fastapi ≥ 0.115, < 1 |
| uvicorn[standard] | 0.46.0 | ≥ 0.30, < 1 |
| pydantic (core) | 2.13.3 (2.46.3) | ≥ 2.9, < 3 |
| numpy | 2.4.4 | ≥ 2.1, < 3 |
| pandas | 3.0.2 | ≥ 3.0, < 4 |
| scipy | 1.17.1 | ≥ 1.14, < 2 |
| openpyxl | 3.1.5 | ≥ 3.1, < 4 |
| python-multipart | 0.0.26 | ≥ 0.0.18, < 0.1 |
| pytest / httpx / ruff (dev) | 9.1.1 / 0.28.1 / 0.15.11 | pytest ≥ 8, < 10; ruff 0.15.x |
| playwright (e2e only) | 1.63.0 | `e2e/requirements.txt` |

The frontend was built and type-checked with:

| Package | Tested | Range in `package.json` |
| --- | --- | --- |
| @angular/core, common, compiler, platform-browser, router | 22.2 | ^22.1.0 |
| @angular/build, @angular/cli, @angular/compiler-cli | 22.2 | ^22.1.0 |
| typescript | 6.0.3 | ~6.0.2 (Angular 22 needs 6.0) |
| rxjs / tslib | 7.8.2 / 2.8.1 | ~7.8.0 / ^2.3.0 |
| plotly.js-dist-min | 3.7.0 | ^3.1.0 |

The first `./dev.sh setup` runs `npm install`, which writes `frontend/package-lock.json` with the exact
version of every npm package. **Commit that file**: from then on `./dev.sh setup` (and CI) use `npm ci`,
which installs exactly what the lockfile says.

To upgrade a Python package, raise its pin in `constraints.txt`, run `./dev.sh setup` and `./dev.sh check`,
and commit the pin together with any fixes. For npm: `cd frontend && npm update`, then `./dev.sh check`
and commit the new lockfile.

## 3 · First run

```bash
# unzip somewhere short (C:\dev\ on Windows - node_modules has deep paths)
cd /c/dev/teglab            # Git Bash path of C:\dev\teglab
./dev.sh doctor             # versions of Python, Node, npm, git, Docker
./dev.sh setup              # .venv + pinned backend + frontend (first time ~2-4 min)
./dev.sh test               # 134 backend tests, ~15 s
./dev.sh api                # terminal 1: API on http://127.0.0.1:8000 (docs at /docs)
./dev.sh ui                 # terminal 2: UI on http://localhost:4200
```

The example cases are loaded into `backend/data/teglab.db` on the first start; delete that file to reset.

## 4 · VS Code

`code .` in the repository folder, then:

1. **Install the recommended extensions** when VS Code asks (Python, Ruff, Angular Language Service, …).
2. **Select the interpreter**: Ctrl+Shift+P → *Python: Select Interpreter* → `.venv` (after `./dev.sh setup`).
3. **Terminal**: on Windows the workspace opens Git Bash (`.vscode/settings.json`; adjust the path there if
   Git for Windows is not in `C:\Program Files\Git`).
4. **Testing panel** (flask icon): discovers the backend tests; run or debug any of them.
5. **Tasks** (Terminal → Run Task…): *dev: API + UI* starts both servers, *check: everything CI checks* is
   the default build task (Ctrl+Shift+B), *test: backend* the default test task.
6. **Run and Debug** (Ctrl+Shift+D): *Backend: API (debug)* with breakpoints in the Python code,
   *Backend: pytest (current file)*, *Pipeline CLI: run the thesis examples*, and *Full stack: API (debug) +
   Chrome* which also starts the dev server and attaches to Chrome for TypeScript breakpoints.

Python files are formatted by Ruff on save with the closest configuration: `backend/pyproject.toml`,
`ruff.toml` (e2e tests) or `tools/ansys/ruff.toml` (keeps the Mechanical script IronPython-compatible).

## 5 · Everyday commands

| Command | What it does |
| --- | --- |
| `./dev.sh api [--port N]` | backend with auto-reload |
| `./dev.sh ui` | Angular dev server, `/api` proxied to :8000 (`frontend/proxy.conf.json`) |
| `./dev.sh test [-k nodal]` | backend tests (arguments go to pytest) |
| `./dev.sh lint` / `./dev.sh fix` | Ruff check + format check / auto-fix + format |
| `./dev.sh contract` | after changing a model in `pipeline/schemas.py`: regenerate the TypeScript models |
| `./dev.sh build` | production build of the UI (`frontend/dist`) |
| `./dev.sh serve` | backend serving the built UI on :8000, like the container |
| `./dev.sh e2e-setup`, `./dev.sh e2e` | browser tests against a running app (see [testing.md](testing.md)) |
| `./dev.sh check` | lint, tests, contract and UI build - run it before every push |
| `./dev.sh docker` | `docker compose up --build` on http://localhost:8000 |

## 6 · Troubleshooting on Windows

* **`python3` opens the Microsoft Store** - `dev.sh` uses the `py` launcher first; if it still picks the
  stub, turn off *App execution aliases* for python in Windows settings.
* **`./dev.sh: Permission denied`** - run `bash dev.sh …`, or once `git update-index --chmod=+x dev.sh`.
* **`$'\r': command not found`** - the script got Windows line endings. `.gitattributes` prevents this for
  clones; for a copied file run `sed -i 's/\r$//' dev.sh`.
* **`Node.js version vXX is not supported by the Angular CLI`** - install Node 24 LTS (`nvm install 24`).
* **Port 8000 or 4200 in use** - stop the other process, or `./dev.sh api --port 8001` and change the
  targets in `frontend/proxy.conf.json` to match.
* **Very long paths during `npm install`** - unzip/clone near the drive root and run
  `git config --global core.longpaths true`.
* **Uploads of real exports are rejected** - the limit is 50 MB per file and 512 MB unpacked per zip
  (`api/pipeline_routes.py`, `pipeline/nodal.py`).
