"""Link checker behavior: HEAD semantics and truncation reporting."""
import webdoctor.checks.links as links


class _Response:
    def __init__(self, status, url):
        self.status_code = status
        self.url = url

    def close(self):
        pass


def test_head_404_is_confirmed_with_get(monkeypatch):
    calls = []

    def _head(*_args, **_kwargs):
        calls.append("HEAD")
        return _Response(404, "https://example.com/missing")

    def _get(*_args, **_kwargs):
        calls.append("GET")
        return _Response(404, "https://example.com/missing")

    monkeypatch.setattr(links.requests, "head", _head)
    monkeypatch.setattr(links.requests, "get", _get)
    result = links.run_links_check(
        "https://example.com", '<a href="https://example.com/missing">Missing</a>'
    )
    assert calls == ["HEAD", "GET"]
    assert result.metrics["broken_count"] == 1
    assert result.metrics["links_checked"] == 1


def test_head_403_get_200_is_not_broken(monkeypatch):
    def _head(*_args, **_kwargs):
        return _Response(403, "https://example.com/protected")

    def _get(*_args, **_kwargs):
        return _Response(200, "https://example.com/protected")

    monkeypatch.setattr(links.requests, "head", _head)
    monkeypatch.setattr(links.requests, "get", _get)
    result = links.run_links_check(
        "https://example.com", '<a href="https://example.com/protected">OK</a>'
    )
    assert result.metrics["broken_count"] == 0


def test_head_405_retries_once_with_get(monkeypatch):
    calls = []

    def _head(*_args, **_kwargs):
        calls.append("HEAD")
        return _Response(405, "https://example.com/no-head")

    def _get(*_args, **_kwargs):
        calls.append("GET")
        return _Response(200, "https://example.com/no-head")

    monkeypatch.setattr(links.requests, "head", _head)
    monkeypatch.setattr(links.requests, "get", _get)
    result = links.run_links_check(
        "https://example.com", '<a href="https://example.com/no-head">OK</a>'
    )
    assert calls == ["HEAD", "GET"]
    assert result.metrics["broken_count"] == 0


def test_truncation_metric_when_more_than_cap(monkeypatch):
    monkeypatch.setattr(links.requests, "head",
                        lambda *_a, **_k: _Response(200, "https://example.com/ok"))
    html = "".join(f'<a href="https://example.com/page-{i}">{i}</a>' for i in range(60))
    result = links.run_links_check("https://example.com", html)
    assert result.metrics["links_found"] == 60
    assert result.metrics["links_checked"] == links.MAX_LINKS_TO_CHECK
    assert result.metrics["links_truncated"] is True
