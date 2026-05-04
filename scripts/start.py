"""Container entrypoint: run migrations, then exec gunicorn.

Pattern mirrors WCC's `backend/start.py` but simpler: we have no Flask-Migrate
to call; init_db.py does the migration apply. After migrations, replace
the current process with gunicorn so signals (SIGTERM from Railway on
deploy) reach the right PID.

Procfile points at the same gunicorn command for `railway run` parity.
The Dockerfile CMD points at this file.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _run_migrations() -> None:
    """Invoke `python scripts/init_db.py` as a child process.

    Subprocess (rather than direct import) so a migration error fails
    fast with a clean traceback before gunicorn would otherwise mask it.
    """
    print("[start.py] applying migrations via init_db.py", flush=True)
    cmd = [sys.executable, os.path.join("scripts", "init_db.py")]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"[start.py] init_db.py failed (exit {result.returncode})", file=sys.stderr)
        sys.exit(result.returncode)
    print("[start.py] migrations OK", flush=True)


def _exec_gunicorn() -> None:
    """Replace the current process with gunicorn.

    Procfile string: gunicorn --workers 1 --threads 4 --timeout 120 ...
    Worker pin at 1 is required because APScheduler runs in-process; with
    multiple workers we'd get duplicate scheduled job runs.
    """
    port = os.environ.get("PORT", "8080")
    args = [
        "gunicorn",
        "--workers", "1",
        "--threads", "4",
        "--timeout", "120",
        "--bind", f"0.0.0.0:{port}",
        "app:create_app()",
    ]
    print(f"[start.py] exec gunicorn on :{port}", flush=True)
    os.execvp("gunicorn", args)


def main() -> int:
    _run_migrations()
    _exec_gunicorn()
    return 0  # not reached; execvp replaces the process.


if __name__ == "__main__":
    raise SystemExit(main())
