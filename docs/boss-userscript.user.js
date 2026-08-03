// ==UserScript==
// @name         BOSS 投递桥接 (投简历 Agent)
// @namespace    https://github.com/coldnight/tou_jianli_agent
// @version      0.2.0
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
 *      read_url / read_content / read_jd).
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

  // --- JD field sanitization for read_jd -----------------------------------
  //
  // JD fields are the ONLY raw page text exception. We strip HTML tags,
  // decode common entities, strip secrets, and cap each field's length.
  // The backend sanitizes again (defense-in-depth), but we do not send
  // raw HTML or secrets over the wire.
  function stripHtml(raw) {
    if (!raw) return "";
    return String(raw)
      .replace(/<[^>]+>/g, "") // strip HTML tags
      .replace(/&nbsp;/g, " ")
      .replace(/&amp;/g, "&")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/\s+/g, " ")
      .trim();
  }

  function sanitizeJdField(raw, maxLen) {
    if (!raw) return null;
    let cleaned = stripHtml(raw);
    if (!cleaned) return null;
    for (const [pat, repl] of SECRET_PATTERNS) {
      cleaned = cleaned.replace(pat, repl);
    }
    cleaned = cleaned.slice(0, maxLen || 8000);
    return cleaned || null;
  }

  // Extract a JD from the BOSS recommended-job page using the
  // boss_recommended_job_v1 profile. This reads scoped DOM fields — never
  // innerHTML or a page dump. The selector profile is versioned so we can
  // adapt to BOSS DOM changes without touching the backend.
  function extractBossRecommendedJobV1(maxTextChars) {
    var budget = maxTextChars || 8000;
    var jd = {
      title: null,
      company: null,
      location: null,
      salary: null,
      experience: null,
      education: null,
      skills: [],
      description: null,
      source_kind: "boss_recommended_job",
      page_url_hash: null,
    };

    // Title: the job title heading on the job detail/recommended card.
    var titleEl =
      document.querySelector(".job-name") ||
      document.querySelector(".job-title") ||
      document.querySelector("h1.name") ||
      document.querySelector('[class*="job-name"]');
    jd.title = sanitizeJdField(titleEl ? titleEl.innerText : null, 200);

    // Company name.
    var companyEl =
      document.querySelector(".company-name") ||
      document.querySelector(".boss-name") ||
      document.querySelector('[class*="company-name"]');
    jd.company = sanitizeJdField(companyEl ? companyEl.innerText : null, 200);

    // Salary range.
    var salaryEl =
      document.querySelector(".salary") ||
      document.querySelector('[class*="salary"]');
    jd.salary = sanitizeJdField(salaryEl ? salaryEl.innerText : null, 100);

    // Location, experience, education — typically in a .job-info / .tag-list
    // section with <li> elements.
    var infoItems = document.querySelectorAll(
      ".job-info li, .tag-list li, .info-primary li, .job-detail .info li",
    );
    var infoTexts = [];
    infoItems.forEach(function (li) {
      var t = stripHtml(li.innerText);
      if (t) infoTexts.push(t);
    });
    // Heuristic: match known patterns for location/experience/education.
    for (var i = 0; i < infoTexts.length && i < 6; i++) {
      var t = infoTexts[i];
      if (
        !jd.location &&
        /[\u4e00-\u9fa5·-]/.test(t) &&
        t.length <= 20 &&
        !/经验|学历|本科|硕士|博士|大专|高中|初中/.test(t)
      ) {
        jd.location = sanitizeJdField(t, 100);
      }
      if (!jd.experience && /经验|年/.test(t)) {
        jd.experience = sanitizeJdField(t, 50);
      }
      if (!jd.education && /学历|本科|硕士|博士|大专|高中|初中|不限/.test(t)) {
        jd.education = sanitizeJdField(t, 50);
      }
    }

    // Skills: tag elements in the job requirements section.
    var skillEls = document.querySelectorAll(
      ".job-tags .tag, .skills .tag, .job-detail .tag-list .tag, .job-sec .tags span",
    );
    var skills = [];
    skillEls.forEach(function (el) {
      var s = sanitizeJdField(el.innerText, 60);
      if (s && skills.length < 30) skills.push(s);
    });
    jd.skills = skills;

    // Description: the main job description text. We read .job-sec-detail or
    // .job-detail .text as innerText (browser already strips HTML from
    // innerText). We cap at the remaining text budget after other fields.
    var descEl =
      document.querySelector(".job-sec-text") ||
      document.querySelector(".job-detail .text") ||
      document.querySelector(".job-sec-detail") ||
      document.querySelector('[class*="job-detail"]');
    if (descEl) {
      var descText = stripHtml(descEl.innerText);
      jd.description = sanitizeJdField(descText, budget);
    }

    return jd;
  }

  // --- Selector resolution --------------------------------------------------
  //
  // The backend sends selector_kind + selector_value (+ selector_name for
  // role selectors). We resolve them using the standard DOM APIs — the same
  // semantics as Playwright's get_by_role/label/placeholder/locator.
  //
  // CSS selectors may contain ``:has-text('...')`` pseudo-selectors which are
  // Playwright-only — native ``querySelectorAll`` throws on them. We strip
  // them and apply text-contains filtering post-query, mirroring the role/label
  // text-filtering pattern. See ``querySelectorAllWithTextFilter`` below.

  // Regex to extract ``:has-text('...')`` or ``:has-text("...")`` from CSS.
  // Captures the text content inside the quotes.
  var HAS_TEXT_RE = /:has-text\(\s*(['"])([^'"]+)\1\s*\)/g;

  // Resolve a CSS selector string that may contain Playwright-only
  // ``:has-text('...')`` pseudo-selectors.
  //
  // The selector string is a comma-separated list of alternatives. For each
  // alternative:
  //   1. Extract all ``:has-text('...')`` segments, capturing the text.
  //   2. Strip them to get valid CSS.
  //   3. Run ``querySelectorAll`` on the cleaned CSS.
  //   4. If the alternative had ``:has-text`` filters, keep only elements whose
  //      ``textContent`` includes *every* extracted text (AND semantics).
  //
  // Alternatives with no ``:has-text`` pass through unchanged. This mirrors
  // the existing ``role`` kind text-filtering pattern.
  function querySelectorAllWithTextFilter(rawCss) {
    var alternatives = String(rawCss).split(",");
    var matched = [];
    for (var i = 0; i < alternatives.length; i++) {
      var alt = alternatives[i].trim();
      if (!alt) continue;
      var texts = [];
      var cleanCss = alt.replace(HAS_TEXT_RE, function (_match, _quote, text) {
        texts.push(text);
        return "";
      });
      var els;
      try {
        els = document.querySelectorAll(cleanCss);
      } catch (_e) {
        // If the cleaned CSS is still invalid (e.g. it became empty after
        // stripping), skip this alternative rather than throwing.
        continue;
      }
      Array.from(els).forEach(function (el) {
        var ok = true;
        for (var t = 0; t < texts.length; t++) {
          if (!(el.textContent || "").includes(texts[t])) {
            ok = false;
            break;
          }
        }
        if (ok) matched.push(el);
      });
    }
    return matched;
  }

  function resolveLocator(ins) {
    const kind = ins.selector_kind;
    const value = ins.selector_value;
    const name = ins.selector_name;

    if (kind === "css") {
      return querySelectorAllWithTextFilter(value);
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
      if (ins.op === "read_jd") {
        // The ONLY raw page text exception. Extracts scoped JD fields
        // (title, company, salary, description, etc.) — never innerHTML.
        // The selector_profile determines which extraction logic to use.
        const profile = ins.selector_profile || "boss_recommended_job_v1";
        if (profile !== "boss_recommended_job_v1") {
          result.success = false;
          result.error = sanitizeError("unknown_selector_profile:" + profile);
          return result;
        }
        const jd = extractBossRecommendedJobV1(ins.max_text_chars || 8000);
        jd.page_url_hash = await sha256Short(window.location.href);
        result.jd = jd;
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

      // --- Communicate flow ops -------------------------------------------
      //
      // These mirror the fill/click logic but use distinct op names so the
      // backend can enforce the communicate click budget (at most one
      // click_immediate_communicate + one send_opening_message per execute
      // call) without the submit-phase gate.
      if (ins.op === "click_immediate_communicate") {
        // Click the "立即沟通" button — same logic as click, separate name.
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
      if (ins.op === "fill_opening_message") {
        // Fill the chat input with the opening message — same logic as fill.
        if (list.length === 0) {
          result.success = false;
          result.error = sanitizeError("element_not_found");
          return result;
        }
        const el = list[0];
        if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
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
      if (ins.op === "send_opening_message") {
        // Click the send button in the chat dialog — same logic as click.
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
      if (ins.op === "read_communication_result") {
        // Check the post-send page state for success / duplicate / error
        // markers. Return a classification string via result.text so the
        // backend can map it to a CommunicationOutcome.
        //
        // The selector strings match the selectors.py constants
        // (COMMUNICATION_SUCCESS_MARKER, COMMUNICATION_DUPLICATE_MARKER,
        // PLATFORM_ERROR_MARKER). We use querySelectorAllWithTextFilter
        // instead of native querySelectorAll because the selectors contain
        // Playwright-only ``:has-text()`` pseudo-selectors that would throw
        // a DOMException in native CSS.
        const successEls = querySelectorAllWithTextFilter(
          ".chat-message:has-text('已发送'), .message-status:has-text('已发送'), .chat-content .message-item:not(.pending)",
        );
        const duplicateEls = querySelectorAllWithTextFilter(
          ".btn-start:has-text('继续沟通'), .chat-operate:has-text('继续沟通')",
        );
        const errorEls = querySelectorAllWithTextFilter(
          ".error-message, .toast-error, .dialog-error",
        );
        if (duplicateEls.length > 0) {
          result.text = "duplicate_detected";
        } else if (errorEls.length > 0) {
          result.text = "platform_failure";
        } else if (successEls.length > 0) {
          result.text = "succeeded";
        } else {
          // Ambiguous state — the backend treats this as "unknown" (hard
          // stop, manual reconciliation).
          result.text = "unknown";
        }
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
      ins = await gmFetch(
        BRIDGE +
          "/next-instruction?page_id=" +
          encodeURIComponent(PAGE_ID),
        {
          method: "GET",
          timeout: 10000, // slightly longer than the backend's 5s long-poll
        },
      );
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
