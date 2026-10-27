"""
app.py - the Flask web application.

Routes:
  GET  /            shows the web page
  POST /api/scrape  receives {"url": "..."} and returns the scraped data as JSON

Nothing is saved: URLs and scraped content only exist while a request runs.
"""

import threading
import time
from collections import defaultdict, deque

from flask import Flask, jsonify, render_template, request

from scraper import ScrapeError, scrape

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024  # requests to us are tiny

# --- very small rate limiter: 10 scrapes per minute per visitor ------------
RATE_LIMIT = 10
RATE_WINDOW_SECONDS = 60
_recent_requests = defaultdict(deque)  # visitor address -> timestamps only
_lock = threading.Lock()


def too_many_requests(visitor):
    now = time.monotonic()
    with _lock:
        stamps = _recent_requests[visitor]
        while stamps and now - stamps[0] > RATE_WINDOW_SECONDS:
            stamps.popleft()
        if len(stamps) >= RATE_LIMIT:
            return True
        stamps.append(now)
        return False


def error_response(message, code, status):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


@app.after_request
def add_security_headers(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    if too_many_requests(request.remote_addr):
        return error_response("Too many requests. Please wait a minute and try again.",
                              "rate_limited", 429)

    data = request.get_json(silent=True)
    url = data.get("url") if isinstance(data, dict) else None

    try:
        result = scrape(url)
    except ScrapeError as error:
        return error_response(error.message, error.code, error.status)
    except Exception:
        # Details go to the terminal only, never to the browser.
        app.logger.exception("Unexpected error while scraping")
        return error_response("Something went wrong on the server. Please try again.",
                              "server_error", 500)

    return jsonify({"success": True, "data": result})


@app.errorhandler(404)
def not_found(_):
    return error_response("Page not found.", "not_found", 404)


@app.errorhandler(405)
def method_not_allowed(_):
    return error_response("That method is not allowed here.", "method_not_allowed", 405)


@app.errorhandler(413)
def too_large(_):
    return error_response("Request too large.", "request_too_large", 413)


@app.errorhandler(500)
def server_error(_):
    return error_response("Something went wrong on the server.", "server_error", 500)


if __name__ == "__main__":
    # debug=False so the interactive debugger can never be exposed.
    app.run(host="127.0.0.1", port=5000, debug=False)
