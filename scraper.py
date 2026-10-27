"""
scraper.py - all the web scraping logic lives in this file.

Flask (app.py) only calls scrape(url) and sends the result back as JSON.
Keeping the two apart makes the code easier to read, test and reuse.

Safety features implemented here:
  * Only http/https URLs on ports 80/443 are accepted.
  * The hostname is resolved and EVERY IP address must be public, so
    localhost, 192.168.x.x, 10.x.x.x, 169.254.x.x etc. are rejected (SSRF guard).
  * Redirects are followed manually (max 3) and each hop is validated again.
  * Request timeouts, a maximum download size and an overall time limit.
  * robots.txt is checked before the page is fetched.
  * We never try to get around logins, CAPTCHAs or anti-bot systems.
"""

import ipaddress
import socket
import time
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# Settings (change these if you want different limits)
# ----------------------------------------------------------------------------
USER_AGENT = "CognifyzLearningScraper/1.0 (educational project)"
REQUEST_TIMEOUT = (5, 10)            # (connect seconds, read seconds)
TOTAL_TIME_LIMIT = 20                # max seconds for one download
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB page limit
MAX_ROBOTS_BYTES = 512 * 1024
MAX_REDIRECTS = 3
MAX_URL_LENGTH = 2048
ALLOWED_PORTS = {None, 80, 443}

MAX_HEADINGS = 30
MAX_PARAGRAPHS = 20
MAX_LINKS = 50
MAX_TEXT_LENGTH = 500
MIN_PARAGRAPH_LENGTH = 20            # skips tiny fragments such as "Menu"

REDIRECT_CODES = {301, 302, 303, 307, 308}
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en",
}


class ScrapeError(Exception):
    """An error whose message is safe to show to the user."""

    def __init__(self, message, code="error", status=400):
        super().__init__(message)
        self.message = message
        self.code = code      # short machine-readable name, e.g. "timeout"
        self.status = status  # HTTP status the Flask app should answer with


# ----------------------------------------------------------------------------
# 1. URL validation (SSRF protection)
# ----------------------------------------------------------------------------
def _is_public_ip(ip_text):
    """True only for globally routable addresses."""
    ip = ipaddress.ip_address(ip_text.split("%")[0])  # drop IPv6 scope id
    if ip.version == 6 and ip.ipv4_mapped:            # ::ffff:127.0.0.1 trick
        ip = ip.ipv4_mapped
    return ip.is_global


def validate_url(raw_url):
    """Check a URL and return it cleaned up, or raise ScrapeError."""
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise ScrapeError("Enter a website address to scrape.", "invalid_url")

    url = raw_url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise ScrapeError("That address is too long.", "invalid_url")
    if any(ch.isspace() for ch in url):
        raise ScrapeError("The address contains spaces. Check it and try again.", "invalid_url")

    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        raise ScrapeError("That doesn't look like a valid web address.", "invalid_url")

    if parsed.scheme not in ("http", "https"):
        raise ScrapeError("Only http:// and https:// addresses are supported.", "invalid_url")
    if not parsed.hostname:
        raise ScrapeError("The address is missing a website name.", "invalid_url")
    if parsed.username or parsed.password:
        raise ScrapeError("Addresses with a username or password are not allowed.", "invalid_url")
    if port not in ALLOWED_PORTS:
        raise ScrapeError("Only the standard web ports (80 and 443) are allowed.", "invalid_url")

    host = parsed.hostname.lower()
    blocked_message = "That address points to a private or internal network, so it can't be scraped."

    try:
        literal_ip = ipaddress.ip_address(host)
        if not literal_ip.is_global:
            raise ScrapeError(blocked_message, "blocked_address")
    except ValueError:
        if host in ("127.1", "2130706433", "0x7f000001"):
            raise ScrapeError(blocked_message, "blocked_address")

    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ScrapeError(blocked_message, "blocked_address")

    # Resolve the name and make sure every address it points to is public.
    try:
        infos = socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        raise ScrapeError("That website could not be found. Check the address for typos.",
                          "dns_error", 502)
    for info in infos:
        if not _is_public_ip(info[4][0]):
            raise ScrapeError(blocked_message, "blocked_address")

    return url


# ----------------------------------------------------------------------------
# 2. Downloading
# ----------------------------------------------------------------------------
def _read_limited(response, max_bytes, deadline):
    """Read the body in chunks and stop if it is too big or too slow."""
    declared = response.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise ScrapeError("That page is too large to scrape (limit is 2 MB).", "too_large", 413)

    body = bytearray()
    for chunk in response.iter_content(chunk_size=8192):
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ScrapeError("That page is too large to scrape (limit is 2 MB).", "too_large", 413)
        if time.monotonic() > deadline:
            raise ScrapeError("The website is responding too slowly.", "timeout", 504)
    return bytes(body)


def _fetch(url, max_bytes):
    """
    Download a URL. Returns (final_url, status_code, headers, body_bytes).
    Redirects are followed by hand so each new address is validated again.
    """
    current = url
    deadline = time.monotonic() + TOTAL_TIME_LIMIT

    for _ in range(MAX_REDIRECTS + 1):
        current = validate_url(current)
        response = None
        try:
            response = requests.get(current, headers=HEADERS, timeout=REQUEST_TIMEOUT,
                                    stream=True, allow_redirects=False)
            location = response.headers.get("Location")
            if response.status_code in REDIRECT_CODES and location:
                current = urljoin(current, location)
                continue
            body = _read_limited(response, max_bytes, deadline)
            return current, response.status_code, response.headers, body
        except requests.exceptions.Timeout:
            raise ScrapeError("The website took too long to respond.", "timeout", 504)
        except requests.exceptions.SSLError:
            raise ScrapeError("The website's security certificate could not be verified.",
                              "ssl_error", 502)
        except requests.exceptions.ConnectionError:
            raise ScrapeError("Could not connect to the website. It may be offline or blocking requests.",
                              "connection_error", 502)
        except requests.exceptions.RequestException:
            raise ScrapeError("The page could not be downloaded.", "request_error", 502)
        finally:
            if response is not None:
                response.close()

    raise ScrapeError("The website redirected too many times.", "too_many_redirects", 502)


def _robots_allows(url):
    """Return False if the site's robots.txt asks us not to fetch this URL."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        _, status, _, body = _fetch(robots_url, MAX_ROBOTS_BYTES)
    except ScrapeError as error:
        if error.code == "blocked_address":
            raise
        return True  # robots.txt unreachable: nothing says we are forbidden
    if status in (401, 403):
        return False  # be conservative, same as Python's own robots parser
    if status != 200:
        return True   # no robots.txt (e.g. 404) means no restrictions
    parser = robotparser.RobotFileParser()
    parser.parse(body.decode("utf-8", errors="ignore").splitlines())
    return parser.can_fetch(USER_AGENT, url)


def _check_status(status):
    """Turn an HTTP error status into a friendly message."""
    if status < 400:
        return
    messages = {
        401: "This page requires a login. This tool does not access protected pages.",
        403: "The website refused access. This tool does not bypass access restrictions.",
        404: "The page was not found (404). Check the address.",
        429: "The website says there have been too many requests. Try again later.",
    }
    message = messages.get(status) or (
        "The website had a problem (error %d). Try again later." % status if status >= 500
        else "The website returned an error (%d)." % status)
    raise ScrapeError(message, "http_error", 502)


# ----------------------------------------------------------------------------
# 3. Parsing
# ----------------------------------------------------------------------------
def _clean(text, limit=MAX_TEXT_LENGTH):
    """Collapse whitespace and cut very long text."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def parse_html(html_bytes, base_url):
    """Extract title, description, headings, paragraphs and links."""
    soup = BeautifulSoup(html_bytes, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()  # their text is code, not content

    title = _clean(soup.title.get_text(" ", strip=True), 200) if soup.title else ""

    description = ""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        description = _clean(meta["content"])

    headings = []
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = _clean(tag.get_text(" ", strip=True), 200)
        if text:
            headings.append({"level": tag.name, "text": text})

    paragraphs = []
    for tag in soup.find_all("p"):
        text = _clean(tag.get_text(" ", strip=True))
        if len(text) >= MIN_PARAGRAPH_LENGTH:
            paragraphs.append(text)

    links, seen = [], set()
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        if urlparse(absolute).scheme not in ("http", "https") or absolute in seen:
            continue
        seen.add(absolute)
        links.append({"text": _clean(tag.get_text(" ", strip=True), 120) or absolute,
                      "url": absolute})

    found = {"headings": len(headings), "paragraphs": len(paragraphs), "links": len(links)}
    headings = headings[:MAX_HEADINGS]
    paragraphs = paragraphs[:MAX_PARAGRAPHS]
    links = links[:MAX_LINKS]

    return {
        "title": title,
        "description": description,
        "headings": headings,
        "paragraphs": paragraphs,
        "links": links,
        "counts": {"headings": len(headings), "paragraphs": len(paragraphs), "links": len(links)},
        "found": found,
        "truncated": any(found[k] > v for k, v in
                         (("headings", MAX_HEADINGS), ("paragraphs", MAX_PARAGRAPHS), ("links", MAX_LINKS))),
    }


# ----------------------------------------------------------------------------
# 4. The one function the Flask app calls
# ----------------------------------------------------------------------------
JS_HINT = ("No readable content was found. If this website builds its pages with JavaScript, "
           "Requests and BeautifulSoup cannot see that content. A browser-automation tool "
           "(such as Playwright or Selenium) would be needed instead.")


def scrape(raw_url):
    """Validate, fetch and parse one page. Raises ScrapeError on any problem."""
    url = validate_url(raw_url)

    if not _robots_allows(url):
        raise ScrapeError("This website's robots.txt asks automated tools not to access this page, "
                          "so it was not scraped.", "robots_disallowed", 403)

    final_url, status, headers, body = _fetch(url, MAX_RESPONSE_BYTES)
    _check_status(status)

    content_type = headers.get("Content-Type", "").lower()
    if content_type and "html" not in content_type:
        raise ScrapeError("That address is not a web page (it returned %s)." %
                          (content_type.split(";")[0] or "unknown content"), "unsupported_page", 415)

    try:
        result = parse_html(body, final_url) if body.strip() else None
    except Exception:
        raise ScrapeError("The page's HTML could not be read.", "parse_error", 502)

    if result is None:
        result = parse_html(b"", final_url)

    result["url"] = final_url
    if not (result["headings"] or result["paragraphs"] or result["links"]):
        result["warning"] = JS_HINT
    return result
