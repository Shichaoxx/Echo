"""Compatibility launcher for existing commands and active workers."""
from echo.queue_jobs import serve

if __name__ == "__main__":
    raise SystemExit(serve())
