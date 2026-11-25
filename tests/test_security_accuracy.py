"""Security header checks must avoid common false positives."""
from types import SimpleNamespace

import webdoctor.checks.security as security


class _Cookie:
    def __init__(self, name, secure=False):
        self.name = name
        self.secure = secure
        self.domain = "example.com"
        self._rest = {}

    def has_nonstandard_attr(self, _name):
        return False


def _response(headers, set_cookies=None):
    raw_headers = dict(headers)
    if set_cookies:
        raw_headers["Set-Cookie"] = set_cookies
    return SimpleNamespace(
        url="https://example.com/",
        headers={k.lower(): v for k, v in headers.items()},
        cookies=[_Cookie("session")] if set_cookies else [],
        raw=SimpleNamespace(headers=SimpleNamespace(items=lambda: list(raw_headers.items()))),
    )


def _ids(result):
    return {i.id for i in result.issues}


def test_csp_frame_ancestors_satisfies_clickjacking_protection(monkeypatch):
    monkeypatch.setattr(
        security.requests, "get",
        lambda *_a, **_k: _response({"Content-Security-Policy": "frame-ancestors 'self'; default-src 'self'"}),
    )
    result = security.run_security_check("https://example.com")
    assert "SEC_XFO_MISSING" not in _ids(result)


def test_short_hsts_max_age_is_flagged(monkeypatch):
    monkeypatch.setattr(
        security.requests, "get",
        lambda *_a, **_k: _response({"Strict-Transport-Security": "max-age=60"}),
    )
    result = security.run_security_check("https://example.com")
    assert "SEC_HSTS_WEAK" in _ids(result)


def test_cookie_missing_samesite_is_flagged(monkeypatch):
    monkeypatch.setattr(
        security.requests, "get",
        lambda *_a, **_k: _response(
            {"Content-Security-Policy": "default-src 'self'"},
            set_cookies="session=abc; Path=/; Secure; HttpOnly",
        ),
    )
    result = security.run_security_check("https://example.com")
    issue = next(i for i in result.issues if i.id == "SEC_INSECURE_COOKIE")
    assert "SameSite" in issue.summary
    assert "HttpOnly" not in issue.summary
