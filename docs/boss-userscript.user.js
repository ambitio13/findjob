// ==UserScript==
// @name         BOSS 投递桥接 (投简历 Agent)
// @namespace    https://github.com/coldnight/tou_jianli_agent
// @version      0.1.0
// @description  Tampermonkey userscript that executes backend-issued instructions on the BOSS直聘 page. No CDP signature, no hard-coded selectors — the backend sends everything.
// @author       tou_jianli_agent
// @match        https://www.zhipin.com/*
// @match        https://zhipin.com/*
// @grant        GM_xmlhttpRequest
// @connect      localhost
// @connect      127.0.0.1
// @run-at       document-idle
// @noframes
// ==/UserScript==

/* eslint-disable no-undef */
/*
 * Userscript Bridge for BOSS直聘 — Instruction Executor
 *
 * Architecture (see `.trellis/tasks/08-03-userscript-bridge-adapter/design.md`):
 *
 * The backend constructs every instruction (op + selector + fill_value). This
 * userscript only:
 *   1. Polls `GET /userscript-bridge/next-instruction` (long-poll, 5s).
 *   2. Resolves the selector in the page's own JS context (no CDP).
 *   3. Executes the op (fill / click / check_visible / count / read_title /
 *      read_url / read_content).
 *   4. Posts back a SANITIZED result (sha256(url), truncated title, stripped
 *      error — never raw cookies/tokens/HTML).
 *   5. Sends a heartbeat every 5s so the backend knows the connection is live.
 *
 * Safety invariants enforced by the BACKEND (this script trusts the backend):
 *   - The backend never sends a `goto`/navigation instruction. This script
 *     has no navigation function at all.
 *   - prepare phase: the backend's UserscriptBossPage.click raises before
 *     sending any click instruction. So a click instruction only arrives
 *     during the submit phase.
 *   - This script clicks AT MOST once per instruction — it does not loop.
 *
 * Security:
 *   - No `X-User-Id` header is sent (the userscript doesn't know it).
 *   - Results carry only sanitized values. The backend applies defense-in-depth
 *     sanitization again on receipt.
 *   - `@connect` restricts GM_xmlhttpRequest to localhost only.
 *   - `@noframes` prevents the script from running in BOSS iframes.
 */
(function () {
  "use strict";

  // --- Configuration --------------------------------------------------------
  //
  // The backend URL is fixed to localhost. In production, the backend is
  // reached via a local reverse proxy / SSH tunnel; the userscript never
  // connects to a remote host directly. Adjust BACKEND_BASE if your dev
  // server uses a different port.
  const BACKEND_BASE = "http://127.0.0.1:8000/api/v1";
  const BRIDGE = BACKEND_BASE + "/userscript-bridge";

  const HEARTBEAT_INTERVAL_MS = 5000; // backend CONNECTION_TIMEOUT_S is 15s
  const POLL_BACKOFF_MS = 1000; // pause between poll cycles on error
  const RESULT_POST_TIMEOUT_MS = 10000;

  // --- Stable per-tab page_id ----------------------------------------------
  //
  // Each tab needs a stable identifier so the backend can bind instructions to
  // this specific tab. We use sessionStorage so the id survives reloads of the
  // same tab but is not shared across tabs (unlike localStorage). If
  // sessionStorage is unavailable (rare), we fall back to a random id.
  const PAGE_ID_KEY = "boss_bridge_page_id";
  function getPageId() {
    try {
      let id = sessionStorage.getItem(PAGE_ID_KEY);
      if (!id) {
        id =
          "page_" +
          Date.now().toString(36) +
          "_" +
          Math.random().toString(36).slice(2, 10);
        sessionStorage.setItem(PAGE_ID_KEY, id);
      }
      return id;
    } catch (_e) {
      return "page_" + Math.random().toString(36).slice(2, 12);
    }
  }
  const PAGE_ID = getPageId();

  // --- SHA-256 helper (for URL hashing) ------------------------------------
  //
  // The raw page URL can carry tracking tokens / referral codes. We hash it
  // with SHA-256 and send only the first 8 hex chars, matching the backend's
  // sanitize_url(). Uses the Web Crypto API available in the page context.
  async function sha256Short(input) {
    try {
      const data = new TextEncoder().encode(input);
      const hashBuf = await crypto.subtle.digest("SHA-256", data);
      const hex = Array.from(new Uint8Array(hashBuf))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
      return "sha256:" + hex.slice(0, 8);
    } catch (_e) {
      return "sha256:unknown";
    }
  }

  // --- Secret-stripping for results ----------------------------------------
  //
  // Defense-in-depth: strip anything that looks like a cookie/token/session
  // from text we post back, even though the backend sanitizes again. The raw
  // title is unlikely to contain secrets, but we do not trust the DOM.
  const SECRET_PATTERNS = [
    [/(bearer\s+)[A-Za-z0-9._-]+/gi, "$1<redacted>"],
    [/(token=)[A-Za-z0-9._-]+/gi, "$1<redacted>"],
    [/(session=)[A-Za-z0-9._-]+/gi, "$1<redacted>"],
    [/(password=)[^\s&]+/gi, "$1<redacted>"],
    [/(authorization:\s*)[A-Za-z0-9._- ]+/gi, "$1<redacted>"],
    [
      /([A-Za-z0-9_]*(?:session|cookie|token|auth)[A-Za-z0-9_]*)=([A-Za-z0-9._\-+/=]+)/gi,
      "$1=<redacted>",
    ],
  ];

  function sanitizeText(raw) {
    if (!raw) return null;
    let cleaned = String(raw).trim();
    for (const [pat, repl] of SECRET_PATTERNS) {
      cleaned = cleaned.replace(pat, repl);
    }
    cleaned = cleaned.slice(0, 120); // backend _TITLE_MAX
    return cleaned || null;
  }

  function sanitizeError(raw) {
    if (!raw) return null;
    let cleaned = String(raw).trim().slice(0, 200);
    for (const [pat, repl] of SECRET_PATTERNS) {
      cleaned = cleaned.replace(pat, repl);
    }
    return cleaned || null;
  }

  // --- Selector resolution --------------------------------------------------
  //
  // The backend sends selector_kind + selector_value (+ selector_name for
  // role selectors). We resolve them using the standard DOM APIs — the same
  // semantics as Playwright's get_by_role/label/placeholder/locator.
  function resolveLocator(ins) {
    const kind = ins.selector_kind;
    const value = ins.selector_value;
    const name = ins.selector_name;

    if (kind === "css") {
      return document.querySelectorAll(value);
    }
    if (kind === "role") {
      // role=button → all <button> elements; filter by accessible name if given.
      const candidates = document.querySelectorAll(
        '[role="button"], button, [role="link"], a, [role="checkbox"], input[type="checkbox"], [role="tab"]',
      );
      if (!name) return candidates;
      return Array.from(candidates).filter((el) =>
        accessibleName(el).includes(name),
      );
    }
    if (kind === "label") {
      // label text → find the associated control.
      const labels = document.querySelectorAll("label");
      const matched = [];
      for (const lbl of labels) {
        if (accessibleName(lbl).includes(value)) {
          const forId = lbl.getAttribute("for");
          if (forId) {
            const ctrl = document.getElementById(forId);
            if (ctrl) matched.push(ctrl);
          }
          const ctrl = lbl.querySelector("input, textarea, select");
          if (ctrl) matched.push(ctrl);
        }
      }
      return matched;
    }
    if (kind === "placeholder") {
      return document.querySelectorAll(
        '[placeholder="' + cssEscape(value) + '"], textarea[placeholder="' +
          cssEscape(value) + '"]',
      );
    }
    return [];
  }

  function accessibleName(el) {
    return (
      el.getAttribute("aria-label") ||
      el.getAttribute("alt") ||
      el.getAttribute("title") ||
      (el.textContent || "").trim()
    );
  }

  // Minimal CSS string escaper for building attribute selectors safely.
  function cssEscape(str) {
    return String(str).replace(/["\\]/g, "\\$&");
  }

  // --- Instruction execution -----------------------------------------------

  async function executeInstruction(ins) {
    // Each op returns a result object with sanitized fields only.
    const result = {
      instruction_id: ins.instruction_id,
      success: true,
      visible: null,
      count: null,
      text: null,
      url: null,
      error: null,
      page_id: PAGE_ID,
    };

    // --- Page binding check ----------------------------------------------
    //
    // The backend sends page_id and expected_url_hash with each instruction.
    // We refuse to execute if either does not match our current state. This
    // prevents a non-target tab from consuming instructions meant for another
    // tab (e.g. when multiple BOSS tabs are open).
    if (ins.page_id && ins.page_id !== PAGE_ID) {
      result.success = false;
      result.error = sanitizeError("page_id_mismatch");
      return result;
    }
    if (ins.expected_url_hash) {
      const currentHash = await sha256Short(window.location.href);
      if (currentHash !== ins.expected_url_hash) {
        result.success = false;
        result.error = sanitizeError("url_hash_mismatch");
        return result;
      }
    }

    try {
      if (ins.op === "read_title") {
        result.text = sanitizeText(document.title);
        return result;
      }
      if (ins.op === "read_url") {
        result.url = await sha256Short(window.location.href);
        return result;
      }
      if (ins.op === "read_content") {
        // Truncated text content of the main area — for diagnostics only.
        const main = document.body ? document.body.innerText : "";
        result.text = sanitizeText(main.slice(0, 200));
        return result;
      }

      // All other ops need a locator.
      const elements = resolveLocator(ins);
      const list = Array.from(elements);

      if (ins.op === "check_visible") {
        const visible = list.some((el) => isElementVisible(el));
        result.visible = visible;
        result.count = list.length;
        return result;
      }
      if (ins.op === "count") {
        result.count = list.length;
        return result;
      }
      if (ins.op === "fill") {
        if (list.length === 0) {
          result.success = false;
          result.error = sanitizeError("element_not_found");
          return result;
        }
        const el = list[0];
        if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
          // Use the native setter matching the element type so React-controlled
          // inputs/textareas pick up the value. Using the wrong prototype's
          // setter (e.g. HTMLInputElement's setter on a TEXTAREA) does not
          // update the value on some React builds.
          const proto =
            el.tagName === "TEXTAREA"
              ? window.HTMLTextAreaElement.prototype
              : window.HTMLInputElement.prototype;
          const nativeValueSetter = Object.getOwnPropertyDescriptor(
            proto,
            "value",
          )?.set;
          if (nativeValueSetter) {
            nativeValueSetter.call(el, ins.fill_value || "");
          } else {
            el.value = ins.fill_value || "";
          }
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        } else if (el.isContentEditable) {
          // contenteditable fallback
          el.textContent = ins.fill_value || "";
          el.dispatchEvent(new InputEvent("input", { bubbles: true }));
        } else {
          result.success = false;
          result.error = sanitizeError("fill_unsupported_element:" + el.tagName);
          return result;
        }
        result.visible = true;
        return result;
      }
      if (ins.op === "click") {
        // The backend only sends a click during the submit phase. We click
        // exactly once — no retry loop, no multi-click.
        if (list.length === 0) {
          result.success = false;
          result.error = sanitizeError("element_not_found");
          return result;
        }
        const el = list[0];
        el.scrollIntoView({ block: "center", behavior: "instant" });
        el.click();
        result.visible = true;
        return result;
      }

      result.success = false;
      result.error = sanitizeError("unknown_op:" + ins.op);
      return result;
    } catch (err) {
      result.success = false;
      result.error = sanitizeError(err && err.message ? err.message : String(err));
      return result;
    }
  }

  function isElementVisible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (Number(style.opacity) === 0) return false;
    return true;
  }

  // --- HTTP helpers (GM_xmlhttpRequest bypasses CORS) ----------------------

  function gmFetch(url, options) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: options.method || "GET",
        url: url,
        headers: options.headers || { "Content-Type": "application/json" },
        data: options.body ? JSON.stringify(options.body) : undefined,
        timeout: options.timeout || 15000,
        onload(resp) {
          if (resp.status >= 200 && resp.status < 300) {
            try {
              resolve(resp.responseText ? JSON.parse(resp.responseText) : {});
            } catch (_e) {
              resolve({});
            }
          } else if (resp.status === 204) {
            resolve(null);
          } else {
            reject(new Error("HTTP " + resp.status));
          }
        },
        onerror() {
          reject(new Error("network_error"));
        },
        ontimeout() {
          reject(new Error("timeout"));
        },
      });
    });
  }

  // --- Heartbeat loop ------------------------------------------------------

  async function sendHeartbeat() {
    try {
      const pageUrlHash = await sha256Short(window.location.href);
      await gmFetch(BRIDGE + "/heartbeat", {
        method: "POST",
        body: {
          page_id: PAGE_ID,
          page_url_hash: pageUrlHash,
          page_title: sanitizeText(document.title),
        },
        timeout: 8000,
      });
    } catch (_e) {
      // Heartbeat failures are non-fatal; the next tick will retry.
    }
  }

  // --- Instruction poll loop -----------------------------------------------

  let running = false;

  // Returns true if an instruction was processed (and the queue should be
  // drained again immediately), false on 204 / error (pause until next tick).
  async function pollOnce() {
    let ins;
    try {
      ins = await gmFetch(BRIDGE + "/next-instruction", {
        method: "GET",
        timeout: 10000, // slightly longer than the backend's 5s long-poll
      });
    } catch (_e) {
      // Network error or backend down — back off and retry.
      return false;
    }
    if (ins === null) return false; // 204 No Content — no instruction queued.

    // Execute and post back the result.
    const result = await executeInstruction(ins);
    try {
      await gmFetch(BRIDGE + "/result", {
        method: "POST",
        body: result,
        timeout: RESULT_POST_TIMEOUT_MS,
      });
    } catch (_e) {
      // If the result post fails, the backend will time out waiting for it
      // (RESULT_TIMEOUT_S=15s) and return a failure result itself. We do not
      // retry — the backend handles the timeout gracefully.
    }
    return true;
  }

  async function pollLoop() {
    if (running) return;
    running = true;
    try {
      // Drain the queue: keep polling until a 204 (no instruction) comes back,
      // so a batch of queued instructions is processed without waiting for the
      // next poll tick.
      while (await pollOnce()) {
        // loop again immediately
      }
    } catch (_e) {
      // Swallow — the loop restarts on the next interval.
    } finally {
      running = false;
    }
  }

  // --- Bootstrap -----------------------------------------------------------

  console.log("[boss-bridge] userscript loaded on", window.location.href, "page_id=", PAGE_ID);

  // Heartbeat: send immediately, then every 5s.
  void sendHeartbeat();
  setInterval(sendHeartbeat, HEARTBEAT_INTERVAL_MS);

  // Instruction poll: poll every second; each poll long-polls for up to 5s on
  // the backend side. This keeps latency low while avoiding a tight busy-loop.
  setInterval(() => {
    void pollLoop();
  }, POLL_BACKOFF_MS);

  // Kick off an immediate poll so a queued instruction is picked up without
  // waiting for the first interval.
  void pollLoop();
})();
