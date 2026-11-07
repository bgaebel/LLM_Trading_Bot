"""The overview's only script, served as ``/app.js`` (Spec 010, ADR-0019).

ADR-0019: one script of our own under ``script-src 'self'`` - no inline script, no eval, no
foreign source. The server still renders every value (and escapes it); the script only fetches
the overview from the same origin every few seconds and swaps the rendered parts in, and it
counts the ages ("vor 12 s") up every second from their ``data-at`` epoch.
"""

JS = r"""// Live overview (Spec 010, ADR-0019) from llm_trading_bot.web.script - holds no data.
"use strict";
(() => {
  const PERIOD = 5000;
  const MAX_WAIT = 60000;
  const DEADLINE = 15000; // one request; a page normally comes in well under a second
  let wait = PERIOD;
  let offset = 0; // server time minus browser time, in seconds
  let timer = 0;

  // Same steps as overview_page.age(); Python's round() rounds half to even.
  // age:start
  function roundHalfEven(x) {
    const r = Math.round(x);
    return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
  }
  function age(seconds) {
    if (seconds < 90) return "vor " + Math.floor(seconds) + " s";
    if (seconds < 90 * 60) return "vor " + roundHalfEven(seconds / 60) + " min";
    if (seconds < 48 * 3600) return "vor " + roundHalfEven(seconds / 3600) + " h";
    return "vor " + roundHalfEven(seconds / 86400) + " Tagen";
  }
  // age:end

  function readServerTime(root) {
    const marker = root.querySelector("[data-now]");
    if (marker) offset = Number(marker.dataset.now) - Date.now() / 1000;
  }

  function tick() {
    const now = Date.now() / 1000 + offset;
    for (const element of document.querySelectorAll("[data-at]")) {
      element.textContent = age(Math.max(0, now - Number(element.dataset.at)));
    }
  }

  function notice(text) {
    let element = document.getElementById("live-problem");
    if (!text) {
      if (element) element.remove();
      return;
    }
    if (!element) {
      element = document.createElement("div");
      element.id = "live-problem";
      element.className = "msg bad";
      element.setAttribute("role", "status");
      document.querySelector("main").prepend(element);
    }
    element.textContent = text;
  }

  // The page apart from what changes every second anyway (ages, the server time marker).
  function signature(node) {
    const copy = node.cloneNode(true);
    for (const element of copy.querySelectorAll("[data-at]")) element.textContent = "";
    for (const element of copy.querySelectorAll("[data-now]")) element.removeAttribute("data-now");
    for (const element of copy.querySelectorAll("#live-problem")) element.remove();
    return new XMLSerializer().serializeToString(copy);
  }

  // Swapping resets horizontal scrolling and drops a text selection: only when the content
  // changed (about once a round), never while the user has text selected, keeping scrollLeft.
  function swap(current, fresh) {
    if (signature(current) === signature(fresh)) return;
    const selection = window.getSelection();
    if (selection && !selection.isCollapsed && current.contains(selection.anchorNode)) return;
    const scrolled = [...current.querySelectorAll(".scroll")].map((e) => e.scrollLeft);
    current.replaceWith(fresh);
    fresh.querySelectorAll(".scroll").forEach((e, i) => {
      if (scrolled[i]) e.scrollLeft = scrolled[i];
    });
  }

  // One fetch at a time: a tab that becomes visible again must not start a second chain.
  let busy = false;

  async function refresh() {
    if (busy) return;
    busy = true;
    window.clearTimeout(timer);
    // A half-dead tunnel can leave a request hanging for minutes: give up after a deadline.
    const controller = new AbortController();
    const deadline = window.setTimeout(() => controller.abort(), DEADLINE);
    try {
      const response = await fetch("/", {
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
      });
      const path = new URL(response.url).pathname;
      if (path === "/login" || path === "/setup") {
        location.assign("/login"); // the session ended: log in again
        return;
      }
      if (!response.ok) throw new Error("HTTP " + response.status);
      const fresh = new DOMParser().parseFromString(await response.text(), "text/html");
      const main = fresh.querySelector("main");
      if (!main) throw new Error("no overview in the answer");
      readServerTime(fresh);
      notice("");
      swap(document.querySelector("main"), document.adoptNode(main));
      // The ticker only changes once a round; replacing it restarts its scroll.
      const track = fresh.querySelector(".ticker-track");
      const shown = document.querySelector(".ticker-track");
      if (track && shown && track.textContent !== shown.textContent) {
        shown.replaceWith(document.adoptNode(track));
      }
      tick();
      wait = PERIOD;
    } catch (error) {
      wait = Math.min(wait * 2, MAX_WAIT);
      notice("Verbindung unterbrochen – nächster Versuch in " + Math.round(wait / 1000) + " s");
    } finally {
      window.clearTimeout(deadline);
      busy = false;
    }
    timer = window.setTimeout(refresh, wait);
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  readServerTime(document);
  tick();
  window.setInterval(tick, 1000);
  timer = window.setTimeout(refresh, PERIOD);
})();
"""
