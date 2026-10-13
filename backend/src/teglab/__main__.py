"""``python -m teglab`` starts the API server (and the built UI if TEGLAB_STATIC is set)."""

import argparse
import os


def main() -> None:
    ap = argparse.ArgumentParser(description="teglab server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default=None, help="SQLite file (default data/teglab.db)")
    ap.add_argument("--static", default=None, help="directory of the built Angular app")
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()
    if args.db:
        os.environ["TEGLAB_DB"] = args.db
    if args.static:
        os.environ["TEGLAB_STATIC"] = args.static
    import uvicorn

    uvicorn.run("teglab.api.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
