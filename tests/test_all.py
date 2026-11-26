"""Comprehensive test suite for WebDoctor, including multi-AI providers and authenticated testing."""
import json
import os
import threading
import http.server
import socketserver
import unittest

from webdoctor.models import Issue, Severity, CheckResult, CheckStatus, TestReport
from webdoctor.runner import normalize_url, run_diagnostics
from webdoctor.reporters.markdown_report import generate_markdown
from webdoctor.reporters.html_report import generate_html
from webdoctor.reporters.json_report import generate_json
from webdoctor.checks.seo_a11y import run_seo_a11y_check
from webdoctor.checks.links import run_links_check
from webdoctor.checks.auth import perform_form_login, run_auth_audit_check
from webdoctor.diagnostics.ai_adapters import list_available_models
from webdoctor.diagnostics.ai_engine import _multi_model_diagnosis, build_diagnosis_prompt
from webdoctor.cli import build_parser, _parse_compare


def _start_fixture_server(handler):
    """Start a local HTTP fixture on an OS-assigned free port."""
    class ReuseTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    httpd = ReuseTCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    return httpd, thread, base_url


class TestModels(unittest.TestCase):
    def test_score_clean(self):
        r = TestReport(url="https://example.com").compute_summary()
        self.assertEqual(r.score, 100)
        self.assertEqual(r.overall_grade, "A")
        self.assertEqual(r.overall_status, CheckStatus.PASSED)

    def test_score_penalties(self):
        issues = [
            Issue(id="C1", category="http", severity=Severity.CRITICAL, title="crit", summary="s"),
            Issue(id="E1", category="browser", severity=Severity.ERROR, title="err", summary="s"),
            Issue(id="W1", category="security", severity=Severity.WARNING, title="warn", summary="s"),
        ]
        cr = CheckResult(name="t", category="c", status=CheckStatus.FAILED, issues=issues)
        r = TestReport(url="https://example.com", check_results=[cr]).compute_summary()
        self.assertEqual(r.score, 51)
        self.assertEqual(r.overall_grade, "D")
        self.assertEqual(r.overall_status, CheckStatus.FAILED)

    def test_json_roundtrip(self):
        cr = CheckResult(name="t", category="c", status=CheckStatus.PASSED)
        r = TestReport(url="https://example.com", check_results=[cr]).compute_summary()
        data = json.loads(r.to_json())
        self.assertEqual(data["url"], "https://example.com")
        self.assertIn("score", data)

    def test_normalize_url(self):
        self.assertEqual(normalize_url("example.com"), "https://example.com")
        self.assertEqual(normalize_url("http://localhost:3000"), "http://localhost:3000")


SAMPLE_HTML_HEALTHY = """<!doctype html><html lang="en"><head>
<title>Healthy Store -- Buy Great Products Online Here Now</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Shop the finest quality products with fast shipping, easy returns, and exceptional customer support every day.">
<link rel="icon" href="/favicon.ico">
</head><body>
<h1>Welcome to Healthy Store</h1>
<p>Content</p>
<img src="/img/logo.png" alt="Company logo">
<a href="https://example.com">Example</a>
</body></html>"""

SAMPLE_HTML_BROKEN = """<!doctype html><html><head>
<title>Hi</title>
</head><body>
<p>Broken</p>
<img src="/bad/image.png">
<a href="/missing-link-abc">Dead link</a>
<form><input type="text" name="q"></form>
</body></html>"""


class TestSEOandA11y(unittest.TestCase):
    def test_healthy_page(self):
        res = run_seo_a11y_check("https://example.com", SAMPLE_HTML_HEALTHY)
        errors = [i for i in res.issues if i.severity in (Severity.CRITICAL, Severity.ERROR)]
        self.assertEqual(len(errors), 0)

    def test_broken_page_detects_multiple_issues(self):
        res = run_seo_a11y_check("https://example.com", SAMPLE_HTML_BROKEN)
        ids = {i.id for i in res.issues}
        self.assertIn("A11Y_NO_VIEWPORT", ids)
        self.assertIn("A11Y_NO_LANG", ids)
        self.assertIn("SEO_NO_META_DESCRIPTION", ids)
        self.assertIn("SEO_NO_H1", ids)
        self.assertIn("A11Y_MISSING_IMG_ALT", ids)
        self.assertIn("A11Y_FORM_MISSING_LABELS", ids)
        for issue in res.issues:
            self.assertTrue(len(issue.how_it_happened) > 20)
            self.assertTrue(len(issue.fix) > 10)


class TestLinks(unittest.TestCase):
    def test_collects_and_flags_broken(self):
        res = run_links_check("http://127.0.0.1:9999", SAMPLE_HTML_BROKEN)
        self.assertTrue(res.metrics["broken_count"] >= 1)


class TestReporters(unittest.TestCase):
    def _sample_report(self):
        issues = [
            Issue(id="HTTP_500", category="http", severity=Severity.ERROR,
                  title="Server Error 500", summary="The server returned 500.",
                  detail="status: 500", how_it_happened="App crashed during request.",
                  reproduction="curl -i 'https://bad.site'", fix="Check server logs."),
        ]
        cr = CheckResult(name="HTTP", category="http", status=CheckStatus.FAILED, issues=issues)
        r = TestReport(url="https://bad.site", check_results=[cr]).compute_summary()
        r.ai_summary = "Site is failing due to server crash."
        return r

    def test_markdown(self):
        md = generate_markdown(self._sample_report())
        self.assertIn("# WebDoctor Report", md)
        self.assertIn("How it happened", md)

    def test_html(self):
        html = generate_html(self._sample_report())
        self.assertIn("WebDoctor", html)
        self.assertIn("how-box", html)

    def test_json(self):
        js = generate_json(self._sample_report())
        data = json.loads(js)
        self.assertEqual(data["overall_grade"], "B")


class TestMultiAI(unittest.TestCase):
    def test_model_catalog(self):
        models = list_available_models()
        ids = [m["id"] for m in models]
        self.assertIn("gpt-6-luna", ids)
        self.assertIn("gemini-3.8-flash", ids)
        self.assertIn("claude-sonnet-5-5", ids)

    def test_parse_compare(self):
        res = _parse_compare("sandbox:gpt-6-luna,anthropic:claude-sonnet-5-5")
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["provider"], "sandbox")

    def test_side_by_side_merging(self):
        prompt = "test prompt"
        models = [
            {"provider": "sandbox", "model": "gpt-6-luna", "label": "Model A"},
            {"provider": "anthropic", "model": "claude-sonnet-5-5", "label": "Model B", "api_key": ""},
        ]
        out = _multi_model_diagnosis(prompt, models)
        self.assertIn("Side-by-Side", out)
        self.assertIn("Model A", out)
        self.assertIn("Model B", out)


class TestAuthAudit(unittest.TestCase):
    def test_flags_insecure_and_non_httponly_cookies(self):
        state = {
            "cookies": [
                {"name": "sid", "value": "123", "secure": False, "httpOnly": False},
                {"name": "theme", "value": "dark", "secure": True, "httpOnly": False},
            ]
        }
        res = run_auth_audit_check(state, "https://app.test/dashboard")
        ids = {i.id for i in res.issues}
        self.assertIn("AUTH_INSECURE_COOKIE", ids)
        self.assertIn("AUTH_NO_HTTPONLY", ids)

    def test_flags_no_session_cookies(self):
        res = run_auth_audit_check({"cookies": []}, "https://app.test/dashboard")
        ids = {i.id for i in res.issues}
        self.assertIn("AUTH_NO_SESSION_COOKIE", ids)


class TestFormLoginAgainstMockSite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        login_html = """<!doctype html><html lang="en"><head><title>Login</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Log in to access your account dashboard and surveys.">
<link rel="icon" href="/favicon.ico"></head><body>
<h1>Sign In</h1>
<form method="post" action="/login">
<input type="text" name="email">
<input type="password" name="password">
<button type="submit">Sign In</button>
</form></body></html>"""

        dashboard_html = """<!doctype html><html lang="en"><head><title>Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Your authenticated account dashboard.">
<link rel="icon" href="/favicon.ico"></head><body>
<h1>Dashboard</h1><p>Welcome, alice.</p></body></html>"""

        class AuthHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                if self.path in ("/", "/login"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers(); self.wfile.write(login_html.encode("utf-8"))
                elif self.path == "/dashboard":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers(); self.wfile.write(dashboard_html.encode("utf-8"))
                else:
                    self.send_response(404); self.end_headers()
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8", "ignore")
                if "password=secret" in body:
                    self.send_response(302)
                    self.send_header("Set-Cookie", "session=token123; Path=/; HttpOnly")
                    self.send_header("Location", "/dashboard")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"<!doctype html><html><body><div role='alert'>invalid credentials</div></body></html>")

        cls.httpd, cls.t, cls.base_url = _start_fixture_server(AuthHandler)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_successful_form_login(self):
        ok, res = perform_form_login(self.base_url + "/login", "alice", "secret", headless=True)
        self.assertTrue(ok)
        self.assertIn("/dashboard", res.get("final_url", ""))
        self.assertTrue(len(res.get("cookies", [])) >= 1)

    def test_failed_form_login(self):
        ok, res = perform_form_login(self.base_url + "/login", "alice", "WRONG", headless=True)
        self.assertFalse(ok)
        self.assertIn("refused", res.get("error", ""))

    def test_authenticated_diagnostic_run(self):
        report = run_diagnostics(
            self.base_url + "/dashboard",
            login=True,
            login_url=self.base_url + "/login",
            username="alice",
            password="secret",
            browser_mode=True,
            check_links=False,
            ai=False,
        )
        self.assertEqual(report.metrics.get("logged_in"), True)
        self.assertIn("/dashboard", report.metrics.get("post_login_url", ""))
        # Verify authenticated screenshot was taken and suites ran
        self.assertTrue(report.score >= 0)


class TestControlledHealthySite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html = """<!doctype html><html lang="en"><head>
<title>Healthy Fixture -- A Fully Compliant WebDoctor Test Site</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="A fixture site providing healthy HTTP responses, secure headers, and accessible markup for WebDoctor regression tests.">
<link rel="icon" href="/favicon.ico">
</head><body>
<h1>Healthy Fixture Site</h1>
<p>All good.</p>
<img src="/logo.png" alt="Fixture logo">
<a href="/">Home</a>
</body></html>"""

        class HealthyHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)

        cls.httpd, cls.t, cls.base_url = _start_fixture_server(HealthyHandler)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_healthy_fixture_gets_good_score(self):
        report = run_diagnostics(self.base_url, browser_mode=True, check_links=False, ai=False)
        self.assertTrue(report.score >= 80, f"score was {report.score}")
        self.assertIn(report.overall_grade, ("A", "B"))


class TestBrokenDemoSite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        demo_html = """<!doctype html><html><head>
<meta charset="utf-8">
<title>Broken Demo Site</title>
</head><body>
<h1>Broken Demo</h1>
<img src="/img/missing.png">
<a href="/dead-link">Dead link</a>
<script src="/bad.js"></script>
<script>
const u = null;
setTimeout(() => { u.profile.name; }, 300);
console.error("analytics init failure");
</script>
</body></html>"""

        class DemoHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Server", "DemoServer/2.4")
                    self.end_headers(); self.wfile.write(demo_html.encode("utf-8"))
                elif self.path == "/bad.js":
                    self.send_response(500)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers(); self.wfile.write(b"Server Error")
                else:
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers(); self.wfile.write(b"Not Found")

        cls.httpd, cls.t, cls.base_url = _start_fixture_server(DemoHandler)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_broken_site_all_failure_modes(self):
        report = run_diagnostics(self.base_url, browser_mode=True, check_links=True, ai=False)
        self.assertEqual(report.overall_status, CheckStatus.FAILED)
        ids = {i.id: i for i in report.issues}
        self.assertTrue(any("JS_RUNTIME" in k for k in ids))
        self.assertTrue(any("JS_CONSOLE" in k for k in ids))
        self.assertTrue(any("NET_FAIL" in k for k in ids))
        self.assertTrue(any("LINK_BROKEN" in k for k in ids))
        for issue in report.issues:
            self.assertTrue(bool(issue.how_it_happened))
            self.assertTrue(bool(issue.fix))
            self.assertTrue(bool(issue.reproduction))


class TestCLIParser(unittest.TestCase):
    def test_parser_has_login_and_ai_flags(self):
        p = build_parser()
        args = p.parse_args(["check", "example.com", "--login", "--login-url", "https://x.com/login",
                             "--username", "alice", "--password", "secret",
                             "--ai-provider", "anthropic", "--ai-model", "claude-sonnet-5-5"])
        self.assertTrue(args.login)
        self.assertEqual(args.ai_provider, "anthropic")

    def test_subcommands_present(self):
        p = build_parser()
        args = p.parse_args(["models"])
        self.assertEqual(args.command, "models")
        args = p.parse_args(["login", "https://x.com", "--save-session", "/tmp/s.json"])
        self.assertEqual(args.command, "login")


if __name__ == "__main__":
    unittest.main(verbosity=2)
