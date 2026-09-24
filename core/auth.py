"""Small HMAC bearer-token verifier for self-contained deployments."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict


class AuthenticationError(ValueError):
    pass


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_token(user_id: str, secret: str, expires_in: int = 3600, now: int | None = None) -> str:
    if not user_id or not secret:
        raise AuthenticationError("user_id 和 secret 不能为空")
    payload = {"sub": user_id, "exp": int(now or time.time()) + expires_in}
    encoded = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64encode(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"v1.{encoded}.{signature}"


def verify_token(token: str, secret: str, now: int | None = None) -> Dict[str, Any]:
    try:
        version, encoded, signature = token.split(".")
        expected = _b64encode(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
        if version != "v1" or not hmac.compare_digest(signature, expected):
            raise AuthenticationError("令牌签名无效")
        payload = json.loads(_b64decode(encoded))
        if int(payload.get("exp", 0)) <= int(now or time.time()):
            raise AuthenticationError("令牌已过期")
        if not str(payload.get("sub", "")).strip():
            raise AuthenticationError("令牌缺少用户身份")
        return payload
    except AuthenticationError:
        raise
    except Exception as ex:
        raise AuthenticationError("令牌格式无效") from ex
