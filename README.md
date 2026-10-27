# Interactive Web Scraper

Cognifyz Technologies Internship, Task 6: an interactive web scraping app built with
Python, Flask, Requests and BeautifulSoup4.

## Overview
Type a public web page address into the page, press **Scrape**, and the app downloads the page,
extracts its title, headings, paragraphs and links, and shows them in clean sections. You can
download the results as a JSON file.

## Features
- URL input with validation (in the browser and again on the server)
- Extracts: page title, meta description, H1/H2/H3 headings, paragraphs, links (made absolute, de-duplicated)
- Limits: 30 headings, 20 paragraphs, 50 links, so results stay readable
- Counts of headings, paragraphs and links; a note appears if results were limited
- Loading indicator, clear success/error/info messages, **Clear results** button
- **Download JSON** and **Re-scrape** buttons
- The same URL is not fetched again unless you press Re-scrape
- Responsive layout for desktop and mobile
- Nothing is stored on the server (no database, no files, no URL logs)

### Safety features
- Only `http://` and `https://` on ports 80/443
- SSRF protection: the hostname is resolved and every IP must be public. `localhost`, `127.x`, `10.x`,
  `172.16-31.x`, `192.168.x`, `169.254.x` (cloud metadata), IPv6 local ranges and similar are rejected.
  Redirects are followed manually and every hop is checked again.
- Timeouts (5 s connect, 10 s read, 20 s total), 2 MB page-size limit, max 3 redirects
- `robots.txt` is checked first; disallowed pages are not scraped
- No login, CAPTCHA or anti-bot bypassing
- Scraped text is shown with `textContent` (never `innerHTML`) so page HTML cannot run in your browser;
  only `http/https` links are clickable
- Errors shown to users are friendly messages; stack traces stay in the terminal
- Simple rate limit (10 scrapes per minute per visitor), Content-Security-Policy headers, `debug=False`

## Technologies
Python 3.9+, Flask, Requests, BeautifulSoup4 (`html.parser`), HTML, CSS, JavaScript, pytest.

## Folder structure
```
web-scraper/
├── app.py              # Flask app: page route + /api/scrape route
├── scraper.py          # All scraping logic (validation, download, parsing)
├── requirements.txt    # Python dependencies
├── README.md
├── .gitignore          # Keeps venv, caches and secrets out of Git
├── templates/
│   └── index.html      # The page
├── static/
│   ├── style.css       # Styling
│   └── script.js       # Browser logic (fetch, render, download)
└── tests/
    ├── conftest.py
    ├── test_scraper.py # Parsing, validation, local-server tests
    ├── test_app.py     # Flask route tests
    └── fixtures/       # Local HTML files used by the tests
```

## Installation (Windows, VS Code terminal)
Open the project folder in VS Code, then open a terminal (`Ctrl + ``). Use **Command Prompt** or
**PowerShell** (the terminal dropdown lets you switch).

1. Check Python is installed:
   ```
   python --version
   ```
2. Create a virtual environment:
   ```
   python -m venv venv
   ```
3. Activate it:
   - Command Prompt: `venv\Scripts\activate`
   - PowerShell: `venv\Scripts\Activate.ps1`

   If PowerShell says scripts are disabled, run this once and try again:
   `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

   You should now see `(venv)` at the start of the prompt.
4. Install dependencies:
   ```
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

## Run the app
```
python app.py
```
Open **http://127.0.0.1:5000** in your browser. Press `Ctrl + C` in the terminal to stop it.

## Example usage
1. Type `https://example.com` and press **Scrape** (typing just `example.com` also works).
2. Read the counts and the Title, Headings, Paragraphs and Links sections.
3. Press **Download JSON** to save the data, **Re-scrape** to fetch again, or **Clear results** to reset.

Good practice pages: `https://example.com`, `https://quotes.toscrape.com`, `https://books.toscrape.com`
(the last two exist for scraping practice).

## Testing
```
python -m pytest -v
```
The tests need no internet. They check parsing, URL validation and private-address blocking, error
handling (404/403/500, timeouts, unreachable hosts, oversized pages, non-HTML pages, empty pages,
redirect loops, redirects to internal addresses), robots.txt handling, and the Flask routes. A small
local HTTP server serves the files in `tests/fixtures/` so the real Requests code is exercised.

Manual checks in the browser:
- `https://example.com` returns data
- `abc def` shows an error; `http://localhost` and `http://192.168.1.1` are rejected
- `https://this-site-does-not-exist-12345.com` shows a "could not be found" error
- **Download JSON** downloads a `.json` file; narrowing the window below ~560 px switches to the mobile layout

## Limitations
- Pages that build their content with JavaScript (many React/Vue sites) may look empty, because only
  the HTML the server sends is read. Those need a browser-automation tool such as Playwright or Selenium.
- Sites that block automated requests will return errors; the app does not try to get around this.
- The address is checked before the connection is made. A very determined attacker could in theory
  change DNS in between (DNS rebinding). For a public deployment you would add IP pinning or an egress proxy.
- The rate limiter and result cache are in memory and reset when you restart the app.
- This is a learning project. For production use, run it behind a proper WSGI server (e.g. Waitress).

## Ethical scraping guidelines
- Read the website's terms of use and `robots.txt` before scraping.
- Scrape only public pages; never bypass logins, paywalls, CAPTCHAs or anti-bot protections.
- Keep requests occasional; do not hammer a server.
- Do not collect personal data, and respect copyright when you reuse content.
- Prefer an official API if the website offers one.
