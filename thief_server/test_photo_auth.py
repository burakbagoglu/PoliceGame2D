import pytest
from fastapi import HTTPException
from starlette.requests import Request

from photo_auth import PhotoAccessGuard


def request_for(host="127.0.0.1"):
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/photo-auth/login",
        "headers": [],
        "client": (host, 50000),
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
    })


def test_signed_operator_token_rejects_tampering():
    guard = PhotoAccessGuard(pin="123456")
    token, csrf = guard.login("123456", request_for())

    assert guard.validate(token) == csrf
    assert guard.validate(token + "broken") is None


def test_missing_pin_disables_gallery():
    guard = PhotoAccessGuard(pin="")

    with pytest.raises(HTTPException) as error:
        guard.login("123456", request_for())

    assert error.value.status_code == 503


def test_login_rate_limit_blocks_sixth_bad_attempt():
    guard = PhotoAccessGuard(pin="123456")
    request = request_for("10.0.0.15")

    for _ in range(5):
        with pytest.raises(HTTPException) as error:
            guard.login("000000", request)
        assert error.value.status_code == 401

    with pytest.raises(HTTPException) as blocked:
        guard.login("000000", request)
    assert blocked.value.status_code == 429
