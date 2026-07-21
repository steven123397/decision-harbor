"""Run Alembic migrations for both platform and analytics databases."""
import subprocess
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent


def run_migrations():
    for name in ("platform", "analytics"):
        config = API_DIR / "alembic" / name / "alembic.ini"
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(config), "upgrade", "head"],
            cwd=str(API_DIR),
        )
        if result.returncode != 0:
            print(f"Migration failed for {name}", file=sys.stderr)
            sys.exit(1)
    print("Migrations complete.")


if __name__ == "__main__":
    run_migrations()
