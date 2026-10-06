"""Create a persistent signing key for VM deployments without a configured key."""

import os
from pathlib import Path
import secrets


def ensure_secret_key(environ=None):
    environ = os.environ if environ is None else environ
    if environ.get("DJANGO_SECRET_KEY"):
        return

    path = Path(environ["DJANGO_SECRET_KEY_FILE"])
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if not path.read_text(encoding="utf-8").strip():
            raise ValueError(f"Signing key file is empty: {path}")
        return

    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(secrets.token_urlsafe(48) + "\n")


if __name__ == "__main__":
    ensure_secret_key()
