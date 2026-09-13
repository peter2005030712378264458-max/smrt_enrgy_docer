from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from .config import get_settings


def verify_internal_token(x_internal_token: str | None = Header(default=None)) -> None:
    expected_token = get_settings().internal_token
    if not expected_token:
        return

    if not x_internal_token or not hmac.compare_digest(x_internal_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token",
        )
