/*
 * script.js - the browser side of the scraper.
 * Safe rendering: all scraped text is inserted with textContent (never innerHTML),
 * and link addresses are only used if they are http/https.
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const form = $("scrape-form");
  const input = $("url-input");
  const scrapeBtn = $("scrape-btn");
  const clearBtn = $("clear-btn");
  const rescrapeBtn = $("rescrape-btn");
  const downloadBtn = $("download-btn");
  const loading = $("loading");
  const message = $("message");
  const results = $("results");

  // Results kept in memory only (lost on refresh or Clear). Stops repeat scraping of one URL.
  const cache = new Map();
  let current = null; // { key, data } currently on screen

  // ---------- helpers ----------
  function normalizeUrl(text) {
    let value = text.trim();
    if (!value) throw new Error("Enter a website address to scrape.");
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(value)) value = "https://" + value; // allow "example.com"
    let url;
    try { url = new URL(value); } catch (e) { throw new Error("That doesn't look like a valid web address."); }
    if (url.protocol !== "http:" && url.protocol !== "https:")
      throw new Error("Only http:// and https:// addresses are supported.");
    if (!url.hostname.includes(".") && !/^\[.*\]$/.test(url.hostname) && url.hostname !== "localhost")
      throw new Error("That doesn't look like a valid web address.");
    return url.href;
  }

  function showMessage(text, type) {
    message.textContent = text;
    message.className = "message " + type;
    message.hidden = false;
  }
  function hideMessage() { message.hidden = true; message.textContent = ""; }

  function setLoading(isLoading) {
    loading.hidden = !isLoading;
    scrapeBtn.disabled = isLoading;
    rescrapeBtn.disabled = isLoading;
    scrapeBtn.textContent = isLoading ? "Scraping…" : "Scrape";
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function fillList(listId, items, build, emptyText) {
    const list = $(listId);
    list.replaceChildren();
    if (!items.length) {
      list.appendChild(el("li", "empty", emptyText));
      return;
    }
    items.forEach((item) => list.appendChild(build(item)));
  }

  function isSafeHttpUrl(value) {
    try { const u = new URL(value); return u.protocol === "http:" || u.protocol === "https:"; }
    catch (e) { return false; }
  }

  // ---------- rendering ----------
  function render(data) {
    $("count-headings").textContent = data.counts.headings;
    $("count-paragraphs").textContent = data.counts.paragraphs;
    $("count-links").textContent = data.counts.links;

    const sourceLink = $("source-link");
    sourceLink.textContent = data.url;
    if (isSafeHttpUrl(data.url)) sourceLink.href = data.url; else sourceLink.removeAttribute("href");

    const note = $("truncated-note");
    note.hidden = !data.truncated;
    if (data.truncated) {
      note.textContent = "Results are limited. The page has " + data.found.headings + " headings, " +
        data.found.paragraphs + " paragraphs and " + data.found.links + " links; only the first ones are shown.";
    }

    $("page-title").textContent = data.title || "(This page has no title)";
    $("page-description").textContent = data.description || "";

    fillList("headings-list", data.headings, (h) => {
      const li = el("li", h.level);
      li.appendChild(el("span", "tag", h.level.toUpperCase()));
      li.appendChild(el("span", "", h.text));
      return li;
    }, "No headings found.");

    fillList("paragraphs-list", data.paragraphs, (p) => el("li", "", p), "No paragraphs found.");

    fillList("links-list", data.links, (l) => {
      const li = el("li");
      if (isSafeHttpUrl(l.url)) {
        const a = el("a", "", l.text);
        a.href = l.url;
        a.target = "_blank";
        a.rel = "noopener noreferrer nofollow";
        li.appendChild(a);
      } else {
        li.appendChild(el("span", "", l.text));
      }
      li.appendChild(el("span", "url", l.url));
      return li;
    }, "No links found.");

    results.hidden = false;
  }

  // ---------- actions ----------
  async function scrape(force) {
    hideMessage();
    let key;
    try { key = normalizeUrl(input.value); }
    catch (e) { showMessage(e.message, "error"); input.focus(); return; }
    input.value = key;

    if (!force && cache.has(key)) {
      current = { key, data: cache.get(key) };
      render(current.data);
      showMessage("You already scraped this page, so the saved result is shown. Use Re-scrape to fetch it again.", "info");
      return;
    }

    setLoading(true);
    try {
      const response = await fetch("/api/scrape", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: key }),
      });
      let payload = null;
      try { payload = await response.json(); } catch (e) { /* not JSON */ }

      if (!response.ok || !payload || !payload.success) {
        results.hidden = true;
        current = null;
        const text = payload && payload.error && payload.error.message
          ? payload.error.message : "The scraper returned an unexpected response.";
        showMessage(text, "error");
        return;
      }
      cache.set(key, payload.data);
      current = { key, data: payload.data };
      render(payload.data);
      if (payload.data.warning) showMessage(payload.data.warning, "info");
      else showMessage("Success! Extracted " + payload.data.counts.headings + " headings, " +
        payload.data.counts.paragraphs + " paragraphs and " + payload.data.counts.links + " links.", "success");
    } catch (e) {
      results.hidden = true;
      showMessage("Could not reach the scraper app. Is the Flask server still running?", "error");
    } finally {
      setLoading(false);
    }
  }

  function downloadJson() {
    if (!current) return;
    const blob = new Blob([JSON.stringify(current.data, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    let host = "page";
    try { host = new URL(current.data.url).hostname.replace(/[^a-z0-9.-]/gi, "_"); } catch (e) { /* keep default */ }
    link.href = URL.createObjectURL(blob);
    link.download = "scrape-" + host + ".json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  function clearResults() {
    cache.clear();
    current = null;
    results.hidden = true;
    hideMessage();
    input.value = "";
    input.focus();
  }

  form.addEventListener("submit", (event) => { event.preventDefault(); scrape(false); });
  rescrapeBtn.addEventListener("click", () => scrape(true));
  downloadBtn.addEventListener("click", downloadJson);
  clearBtn.addEventListener("click", clearResults);
})();
