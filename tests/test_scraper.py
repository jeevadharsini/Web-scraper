"""
Tests for scraper.py.

* Unit tests: parsing, URL validation (no internet needed).
* Local-server tests: a tiny HTTP server on 127.0.0.1 serves the fixture files so the
  real Requests code runs. validate_url is patched ONLY in those tests, because the
  real one (correctly) refuses localhost.
"""
import http.server
import os
import threading
import time

import pytest

import scraper
from scraper import ScrapeError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def read_fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


# ---------------------------------------------------------------- parsing
def test_parse_sample_page():
    data = scraper.parse_html(read_fixture("sample.html"), "https://example.com/start")
    assert data["title"] == "Sample Page & Test"
    assert data["description"] == "A fixture page for testing."
    assert [(h["level"], h["text"]) for h in data["headings"]] == [
        ("h1", "Main Heading"), ("h2", "Second level"), ("h3", "Third level")]
    assert len(data["paragraphs"]) == 2          # "Hi" is too short
    assert "should not appear" not in str(data)  # script text removed
    urls = [l["url"] for l in data["links"]]
    assert urls == ["https://example.com/about", "https://other.example.org/page",
                    "https://example.com/img"]  # duplicate, #top, javascript:, mailto:, ftp: all skipped
    assert data["counts"] == {"headings": 3, "paragraphs": 2, "links": 3}
    assert data["truncated"] is False


def test_parse_keeps_html_as_plain_text():
    data = scraper.parse_html(read_fixture("sample.html"), "https://example.com/")
    assert any("<b>not bold</b>" in p for p in data["paragraphs"])  # kept as text, escaped by frontend


def test_parse_empty_page():
    data = scraper.parse_html(read_fixture("empty.html"), "https://example.com/")
    assert data["counts"] == {"headings": 0, "paragraphs": 0, "links": 0}
    assert data["title"] == ""


def test_parse_garbage_does_not_crash():
    data = scraper.parse_html(b"<<<div<p>>>> \xff\xfe broken <a href=", "https://example.com/")
    assert "counts" in data


def test_limits_are_applied():
    html = "<html><body>" + "".join(
        f"<h2>H{i}</h2><p>Paragraph number {i} is long enough</p><a href='/p{i}'>L{i}</a>"
        for i in range(100)) + "</body></html>"
    data = scraper.parse_html(html.encode(), "https://example.com/")
    assert data["counts"] == {"headings": scraper.MAX_HEADINGS,
                              "paragraphs": scraper.MAX_PARAGRAPHS, "links": scraper.MAX_LINKS}
    assert data["found"] == {"headings": 100, "paragraphs": 100, "links": 100}
    assert data["truncated"] is True


# ------------------------------------------------------- URL validation
@pytest.mark.parametrize("bad", [
    "", "   ", None, 123, "not a url", "ftp://example.com", "file:///etc/passwd",
    "javascript:alert(1)", "https://", "https://user:pw@example.com", "http://example.com:8080",
    "http://" + "a" * 3000 + ".com",
])
def test_rejects_invalid_urls(bad):
    with pytest.raises(ScrapeError) as exc:
        scraper.validate_url(bad)
    assert exc.value.code == "invalid_url"


@pytest.mark.parametrize("blocked", [
    "http://localhost", "http://localhost/", "https://foo.localhost", "http://127.0.0.1",
    "http://127.1", "http://2130706433", "http://0x7f000001", "http://0.0.0.0",
    "http://10.0.0.5", "http://172.16.0.1", "http://192.168.1.1", "http://169.254.169.254/latest/meta-data",
    "http://[::1]", "http://[::ffff:127.0.0.1]", "http://[fe80::1]", "http://[fc00::1]",
    "http://100.64.0.1", "http://printer.local",
])
def test_blocks_private_and_internal_addresses(blocked):
    with pytest.raises(ScrapeError) as exc:
        scraper.validate_url(blocked)
    assert exc.value.code == "blocked_address"


def test_dns_failure(monkeypatch):
    def boom(*a, **k):
        raise scraper.socket.gaierror("nope")
    monkeypatch.setattr(scraper.socket, "getaddrinfo", boom)
    with pytest.raises(ScrapeError) as exc:
        scraper.validate_url("https://does-not-exist.example")
    assert exc.value.code == "dns_error"


def test_hostname_resolving_to_private_ip_is_blocked(monkeypatch):
    monkeypatch.setattr(scraper.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 443))])
    with pytest.raises(ScrapeError) as exc:
        scraper.validate_url("https://sneaky.example.com")
    assert exc.value.code == "blocked_address"


def test_accepts_public_ip_resolution(monkeypatch):
    monkeypatch.setattr(scraper.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    assert scraper.validate_url(" https://example.com/x ") == "https://example.com/x"


# ------------------------------------------------- local fixture server
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body=b"", ctype="text/html; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path
        if path == "/robots.txt":
            return self._send(200, b"User-agent: *\nDisallow: /private\n", "text/plain")
        if path == "/sample":
            return self._send(200, read_fixture("sample.html"))
        if path == "/empty":
            return self._send(200, read_fixture("empty.html"))
        if path == "/jsonly":
            return self._send(200, read_fixture("js_only.html"))
        if path == "/zero":
            return self._send(200, b"")
        if path == "/private":
            return self._send(200, read_fixture("sample.html"))
        if path == "/notfound":
            return self._send(404, b"nope")
        if path == "/forbidden":
            return self._send(403, b"no")
        if path == "/boom":
            return self._send(500, b"err")
        if path == "/json":
            return self._send(200, b'{"a": 1}', "application/json")
        if path == "/big":
            return self._send(200, b"<p>" + b"x" * (scraper.MAX_RESPONSE_BYTES + 10) + b"</p>")
        if path == "/redirect":
            return self._send(302, b"", extra={"Location": "/sample"})
        if path == "/loop":
            return self._send(302, b"", extra={"Location": "/loop"})
        if path == "/to-internal":
            return self._send(302, b"", extra={"Location": "http://169.254.169.254/"})
        if path == "/slow":
            time.sleep(3)
            return self._send(200, b"<p>late</p>")
        return self._send(404, b"")


@pytest.fixture()
def server(monkeypatch):
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    real_validate = scraper.validate_url

    def allow_loopback_only(url):
        # Let the test server through, but keep real validation for everything else
        # (so the redirect-to-internal test is still genuinely checked).
        if url.startswith("http://127.0.0.1"):
            return url
        return real_validate(url)

    monkeypatch.setattr(scraper, "validate_url", allow_loopback_only)
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_scrape_valid_page(server):
    data = scraper.scrape(server + "/sample")
    assert data["title"] == "Sample Page & Test"
    assert data["counts"]["headings"] == 3
    assert data["counts"]["links"] == 3
    assert "warning" not in data


def test_scrape_follows_safe_redirect(server):
    data = scraper.scrape(server + "/redirect")
    assert data["url"].endswith("/sample")


def test_redirect_to_internal_address_is_blocked(server):
    with pytest.raises(ScrapeError) as exc:
        scraper.scrape(server + "/to-internal")
    assert exc.value.code == "blocked_address"


def test_redirect_loop(server):
    with pytest.raises(ScrapeError) as exc:
        scraper.scrape(server + "/loop")
    assert exc.value.code == "too_many_redirects"


def test_empty_page_gives_warning_not_crash(server):
    for path in ("/empty", "/zero", "/jsonly"):
        data = scraper.scrape(server + path)
        assert data["counts"] == {"headings": 0, "paragraphs": 0, "links": 0}
        assert "JavaScript" in data["warning"]


@pytest.mark.parametrize("path,code", [("/notfound", "http_error"), ("/forbidden", "http_error"),
                                       ("/boom", "http_error"), ("/json", "unsupported_page"),
                                       ("/big", "too_large")])
def test_error_pages(server, path, code):
    with pytest.raises(ScrapeError) as exc:
        scraper.scrape(server + path)
    assert exc.value.code == code


def test_robots_txt_is_respected(server):
    with pytest.raises(ScrapeError) as exc:
        scraper.scrape(server + "/private")
    assert exc.value.code == "robots_disallowed"


def test_timeout(server, monkeypatch):
    monkeypatch.setattr(scraper, "REQUEST_TIMEOUT", (1, 1))
    with pytest.raises(ScrapeError) as exc:
        scraper.scrape(server + "/slow")
    assert exc.value.code == "timeout"


def test_unreachable_server(monkeypatch):
    monkeypatch.setattr(scraper, "validate_url", lambda u: u)
    with pytest.raises(ScrapeError) as exc:
        scraper.scrape("http://127.0.0.1:9/")  # nothing listens on port 9
    assert exc.value.code == "connection_error"
