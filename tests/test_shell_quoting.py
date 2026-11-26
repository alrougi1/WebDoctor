"""Reproduction commands must be safe to copy/paste for any URL."""
import shlex

from webdoctor.checks.seo_a11y import run_seo_a11y_check
from webdoctor.shell import quote


def test_quote_matches_shlex():
    value = "https://example.com/a'b?q=1&x=2"
    assert quote(value) == shlex.quote(value)


def test_reproduction_commands_shell_quote_urls():
    url = "https://example.com/a'b?q=1&x=2"
    result = run_seo_a11y_check(url, "<html><head></head><body></body></html>")
    curl_repros = [i.reproduction for i in result.issues if i.reproduction.startswith("curl")]
    assert curl_repros
    for reproduction in curl_repros:
        assert shlex.quote(url) in reproduction
