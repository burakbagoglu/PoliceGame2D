"""Fotoğraf galerisi için küçük, LAN uyumlu operatör oturum koruması."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class PhotoAccessGuard:
    COOKIE_NAME = "police_photo_admin"

    def __init__(self, pin: str | None = None, ttl_seconds: int = 8 * 60 * 60):
        self.pin = str(pin if pin is not None else os.environ.get("THIEF_PHOTO_ADMIN_PIN", ""))
        self.ttl_seconds = max(300, int(ttl_seconds))
        secret = os.environ.get("THIEF_PHOTO_AUTH_SECRET")
        self._secret = secret.encode("utf-8") if secret else secrets.token_bytes(32)
        self.secure_cookie = os.environ.get("THIEF_PHOTO_COOKIE_SECURE", "false").lower() in {
            "1", "true", "yes",
        }
        self._attempts: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=10))
        self._lock = threading.RLock()

    @property
    def configured(self) -> bool:
        return len(self.pin) >= 6

    @staticmethod
    def _encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def _signature(self, payload: str) -> str:
        digest = hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest()
        return self._encode(digest)

    def _client_key(self, request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def login(self, pin: str, request: Request) -> tuple[str, str]:
        if not self.configured:
            raise HTTPException(
                status_code=503,
                detail="Fotoğraf yönetici PIN'i ayarlanmamış",
            )
        client_key = self._client_key(request)
        now = time.time()
        with self._lock:
            attempts = self._attempts[client_key]
            while attempts and now - attempts[0] > 300:
                attempts.popleft()
            if len(attempts) >= 5:
                raise HTTPException(status_code=429, detail="Çok fazla hatalı deneme; 5 dakika bekleyin")
            if not hmac.compare_digest(str(pin), self.pin):
                attempts.append(now)
                raise HTTPException(status_code=401, detail="Operatör PIN'i yanlış")
            attempts.clear()
        expires = int(now + self.ttl_seconds)
        csrf = secrets.token_urlsafe(24)
        payload = f"{expires}.{csrf}"
        return f"{payload}.{self._signature(payload)}", csrf

    def validate(self, token: str) -> str | None:
        try:
            expires_raw, csrf, signature = str(token or "").split(".", 2)
            payload = f"{expires_raw}.{csrf}"
            if not hmac.compare_digest(signature, self._signature(payload)):
                return None
            if int(expires_raw) < int(time.time()):
                return None
            if len(csrf) < 20:
                return None
            return csrf
        except (TypeError, ValueError):
            return None

    def authorize(self, request: Request, *, write: bool = False) -> str:
        if not self.configured:
            raise HTTPException(status_code=503, detail="Fotoğraf yönetici PIN'i ayarlanmamış")
        csrf = self.validate(request.cookies.get(self.COOKIE_NAME, ""))
        if not csrf:
            raise HTTPException(status_code=401, detail="Fotoğraf galerisi için giriş gerekli")
        if write and not hmac.compare_digest(request.headers.get("X-Photo-CSRF", ""), csrf):
            raise HTTPException(status_code=403, detail="Geçersiz işlem doğrulaması")
        return csrf
