"""Tests for the Flask routes (scraper.scrape is replaced by fakes)."""
import pytest

import app as app_module
from scraper import ScrapeError


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    app_module._recent_requests.clear()
    return app_module.app.test_client()


def test_home_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Interactive Web Scraper" in response.data
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_static_files(client):
    assert client.get("/static/script.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_success(client, monkeypatch):
    fake = {"url": "https://example.com/", "title": "T", "headings": [], "paragraphs": [], "links": [],
            "description": "", "counts": {"headings": 0, "paragraphs": 0, "links": 0},
            "found": {"headings": 0, "paragraphs": 0, "links": 0}, "truncated": False}
    monkeypatch.setattr(app_module, "scrape", lambda url: fake)
    response = client.post("/api/scrape", json={"url": "https://example.com"})
    assert response.status_code == 200
    assert response.get_json() == {"success": True, "data": fake}


def test_scrape_error_is_returned_as_json(client, monkeypatch):
    def fail(url):
        raise ScrapeError("Nope.", "timeout", 504)
    monkeypatch.setattr(app_module, "scrape", fail)
    response = client.post("/api/scrape", json={"url": "https://example.com"})
    assert response.status_code == 504
    assert response.get_json()["error"] == {"code": "timeout", "message": "Nope."}


def test_unexpected_error_hides_details(client, monkeypatch):
    def crash(url):
        raise RuntimeError("secret internal detail /home/user/app.py")
    monkeypatch.setattr(app_module, "scrape", crash)
    response = client.post("/api/scrape", json={"url": "https://example.com"})
    assert response.status_code == 500
    assert b"secret" not in response.data and b"Traceback" not in response.data


@pytest.mark.parametrize("payload", [None, {}, {"url": 5}, {"url": ""}, [1, 2]])
def test_bad_request_bodies(client, payload):
    response = client.post("/api/scrape", json=payload) if payload is not None \
        else client.post("/api/scrape", data="not json", content_type="text/plain")
    assert response.status_code == 400
    assert response.get_json()["success"] is False


def test_real_validation_blocks_localhost(client):
    response = client.post("/api/scrape", json={"url": "http://127.0.0.1/"})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "blocked_address"


def test_wrong_method_and_missing_page(client):
    assert client.get("/api/scrape").status_code == 405
    assert client.get("/nope").status_code == 404


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(app_module, "scrape", lambda url: (_ for _ in ()).throw(ScrapeError("x", "x", 400)))
    codes = [client.post("/api/scrape", json={"url": "https://example.com"}).status_code
             for _ in range(app_module.RATE_LIMIT + 2)]
    assert codes[-1] == 429
