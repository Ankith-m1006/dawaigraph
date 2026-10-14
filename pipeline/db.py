"""FalkorDB connection from environment variables (or a local .env file)."""
import os
import pathlib

from falkordb import FalkorDB

ENV_FILES = [
    pathlib.Path(__file__).resolve().parent.parent / ".env",
    pathlib.Path(__file__).resolve().parent.parent.parent / ".falkordb.env",
]


def _load_env():
    for f in ENV_FILES:
        if f.exists():
            for line in f.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def connect():
    _load_env()
    return FalkorDB(
        host=os.environ.get("FALKORDB_HOST", "localhost"),
        port=int(os.environ.get("FALKORDB_PORT", "6379")),
        username=os.environ.get("FALKORDB_USERNAME") or None,
        password=os.environ.get("FALKORDB_PASSWORD") or None,
    )
