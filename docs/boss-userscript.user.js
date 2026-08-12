// ==UserScript==
// @name         BOSS 投递桥接 (投简历 Agent)
// @namespace    https://github.com/coldnight/tou_jianli_agent
// @version      0.4.0
// @description  Tampermonkey userscript that executes backend-issued instructions on the BOSS直聘 page. No CDP signature. Selectors (including marker groups) are sent from the backend via extra_selectors.
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

  // Shared bridge channel token (Phase 0 security guardrail). Must match the
  // backend's BOSS_BRIDGE_CHANNEL_TOKEN when that is configured; leave as ""
  // for local dev backends without a token. The token is only ever sent as
  // the X-Bridge-Token header to the local backend — never logged, never in
  // result payloads.
  const BRIDGE_TOKEN = "";

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
    [/(authorization:\s*)[A-Za-z0-9._\- ]+/gi, "$1<redacted>"],
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

    // Title: the job title heading on the job detail/recommended card. On the
    // current detail page the title is ``p.name`` inside the banner —
    // ``span.name`` is reserved for company/location, so ``p.name`` must be
    // tried before any bare ``.name`` selector.
    var titleEl =
      document.querySelector(".job-name") ||
      document.querySelector(".job-title") ||
      document.querySelector("h1.name") ||
      document.querySelector('[class*="job-name"]') ||
      document.querySelector(".job-banner p.name") ||
      document.querySelector(".info-primary p.name");
    jd.title = sanitizeJdField(titleEl ? titleEl.innerText : null, 200);

    // Company name. ``.sider-company .company-info`` is the current detail
    // page's company card. Bare ``.company-name`` is probed LAST: it also
    // matches hidden "similar jobs" cards at the bottom of the page.
    var companyEl =
      document.querySelector(".sider-company .company-info") ||
      document.querySelector(".boss-name") ||
      document.querySelector('[class*="company-name"]');
    jd.company = sanitizeJdField(companyEl ? companyEl.innerText : null, 200);

    // Salary range.
    var salaryEl =
      document.querySelector(".salary") ||
      document.querySelector('[class*="salary"]');
    jd.salary = sanitizeJdField(salaryEl ? salaryEl.innerText : null, 100);

    // Location, experience, education. On the current detail page the banner
    // carries dedicated elements (``.text-city`` / ``.text-experiece`` — the
    // misspelling is BOSS's own class name — / ``.text-degree``). Probe those
    // first; ``[class*=]`` wildcards survive BOSS class renames better than
    // exact descendant selectors.
    var locationEl =
      document.querySelector('[class*="text-city"]') ||
      document.querySelector('[class*="location"]');
    if (locationEl) {
      jd.location = sanitizeJdField(locationEl.innerText, 100);
    }
    var experienceEl = document.querySelector('[class*="text-exper"]');
    if (experienceEl) {
      jd.experience = sanitizeJdField(experienceEl.innerText, 50);
    }
    var educationEl = document.querySelector('[class*="text-degree"]');
    if (educationEl) {
      jd.education = sanitizeJdField(educationEl.innerText, 50);
    }

    // Fallback: .job-info / .tag-list style li sections. Each li is split
    // into tokens first so a mixed "city + experience" item no longer gets
    // discarded whole by the exclusion patterns.
    var infoItems = document.querySelectorAll(
      ".job-info li, .tag-list li, .info-primary li, .job-detail .info li",
    );
    var infoTexts = [];
    infoItems.forEach(function (li) {
      var t = stripHtml(li.innerText);
      if (t) infoTexts.push(t);
    });
    var EXCLUDE_RE = /经验|学历|本科|硕士|博士|大专|高中|初中|不限|年|薪|K|k/;
    for (var i = 0; i < infoTexts.length && i < 6; i++) {
      var tokens = infoTexts[i].split(/[\s\n·|,，]+/);
      for (var j = 0; j < tokens.length; j++) {
        var t = tokens[j];
        if (!t) continue;
        if (
          !jd.location &&
          /^[\u4e00-\u9fa5A-Za-z0-9·\-]+$/.test(t) &&
          t.length <= 12 &&
          !EXCLUDE_RE.test(t)
        ) {
          jd.location = sanitizeJdField(t, 100);
        }
        if (!jd.experience && /经验|年/.test(t)) {
          jd.experience = sanitizeJdField(t, 50);
        }
        // "经验不限" contains 不限 but is not an education value.
        if (!jd.education && !/经验/.test(t) && /学历|本科|硕士|博士|大专|高中|初中|不限/.test(t)) {
          jd.education = sanitizeJdField(t, 50);
        }
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

  // --- In-place pane extraction (boss_recommended_pane_v1) ----------------
  //
  // On the recommended list page (/web/geek/jobs) the right-hand detail pane
  // switches in place when a card is clicked — no navigation occurs. This
  // profile reads JD fields scoped to `.job-detail-container .job-detail-box`,
  // per probe-evidence.md §3. Excluded forever: `.job-boss-info` (recruiter
  // PII), `.c-job-tools`, `.c-hot-link`, `.c-breadcrumb`, `.job-detail-op`
  // (buttons). Company is NOT in the pane (taken from the scan candidate).
  function extractBossRecommendedPaneV1(maxTextChars) {
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

    var pane = document.querySelector(".job-detail-container");
    if (!pane) return jd;

    // Title + salary: `.job-detail-header .job-header-info`
    var headerInfo = pane.querySelector(".job-header-info");
    jd.title = sanitizeJdField(
      headerInfo ? headerInfo.innerText : null,
      200,
    );
    var salaryEl = headerInfo
      ? headerInfo.querySelector("[class*=salary]")
      : null;
    jd.salary = sanitizeJdField(
      salaryEl ? salaryEl.innerText : null,
      100,
    );

    // Description: `.job-detail-body p.desc`
    var descEl = pane.querySelector(".job-detail-body p.desc");
    if (descEl) {
      jd.description = sanitizeJdField(
        stripHtml(descEl.innerText),
        budget,
      );
    }

    // Tags / skills: `.job-detail-body ul.job-label-list li`
    var tagEls = pane.querySelectorAll(
      ".job-detail-body ul.job-label-list li",
    );
    var tags = [];
    tagEls.forEach(function (el) {
      var t = sanitizeJdField(el.innerText, 60);
      if (t && tags.length < 30) tags.push(t);
    });
    jd.skills = tags;

    // Location: `.job-detail-body .job-address`
    var addrEl = pane.querySelector(".job-detail-body .job-address");
    if (addrEl) {
      jd.location = sanitizeJdField(addrEl.innerText, 100);
    }

    // Company: NOT available in the pane (probe-evidence §3).
    // The service merges the candidate's company after read.
    jd.company = null;

    return jd;
  }

  // --- Scan candidate cache ------------------------------------------------
  //
  // In-memory map: job_key -> card element. Populated by scan_visible_jobs,
  // consumed by open_job_by_key. Cleared on URL change or new scan. With
  // zero-navigation the cache survives the entire run.
  var lastScanCandidates = {};
  var lastScanUrlHash = null;

  function clearScanCacheIfUrlChanged() {
    var currentHash = sha256ShortSync(window.location.href);
    if (lastScanUrlHash !== null && lastScanUrlHash !== currentHash) {
      lastScanCandidates = {};
      lastScanUrlHash = null;
    }
  }

  // Synchronous sha256 fallback for cache management (non-async context).
  // Uses the same Web Crypto API but returns a placeholder on failure.
  function sha256ShortSync(input) {
    try {
      // crypto.subtle.digest is async-only, so we use a simple hash for
      // cache invalidation purposes. This is NOT used for privacy-sensitive
      // data — only to detect URL changes between scans.
      var str = String(input);
      var h = 0;
      for (var i = 0; i < str.length; i++) {
        h = ((h << 5) - h + str.charCodeAt(i)) | 0;
      }
      return "hash:" + h;
    } catch (_e) {
      return "hash:unknown";
    }
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
        result.text = sanitizeText(main.slice(0, ins.max_text_chars || 200));
        return result;
      }
      if (ins.op === "read_jd") {
        // The ONLY raw page text exception. Extracts scoped JD fields
        // (title, company, salary, description, etc.) — never innerHTML.
        // The selector_profile determines which extraction logic to use.
        const profile = ins.selector_profile || "boss_recommended_job_v1";
        if (profile === "boss_recommended_pane_v1") {
          const jd = extractBossRecommendedPaneV1(ins.max_text_chars || 8000);
          jd.page_url_hash = await sha256Short(window.location.href);
          result.jd = jd;
          return result;
        }
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

      // --- Discovery ops (zero-navigation master-detail flow) --------------
      //
      // These ops do NOT use resolveLocator — they operate on the
      // recommended-list card/pane DOM directly. See design.md §Userscript
      // Contract and probe-evidence.md for selector evidence.

      if (ins.op === "scan_visible_jobs") {
        // READ-ONLY scan of visible div.job-card-wrap cards. Derive job_key
        // from sha256(a.job-name href path). Return sanitized candidates
        // only — never raw hrefs, HTML, or contact names.
        clearScanCacheIfUrlChanged();
        var maxItems = ins.max_items || 50;

        var cardEls = document.querySelectorAll("div.job-card-wrap");
        if (cardEls.length === 0) {
          // Distinguish "not on the recommended list page" (no card
          // container at all) from "page is valid but empty". The geek
          // recommendation list page always has the master-detail structure;
          // if no cards are found the page shape is wrong.
          result.success = false;
          result.error = sanitizeError("not_recommended_list_page");
          return result;
        }

        var candidates = [];
        var newCache = {};
        for (var ci = 0; ci < cardEls.length && candidates.length < maxItems; ci++) {
          var card = cardEls[ci];
          if (!isElementVisible(card)) continue;

          // job_key: sha256 of a.job-name href path (query stripped).
          var nameLink = card.querySelector("a.job-name");
          if (!nameLink) continue;
          var href = nameLink.getAttribute("href") || "";
          // Strip query string, keep only the path portion.
          var pathOnly = href.split("?")[0].split("#")[0];
          if (!pathOnly) continue;
          var jobKey = await sha256Short(pathOnly);

          // Field selectors (probe-evidence §2).
          var titleEl = card.querySelector(".job-name");
          var salaryEl = card.querySelector(".job-salary");
          var companyEl = card.querySelector("[class*=company]");
          var areaEl = card.querySelector("[class*=area]");
          var tagEls2 = card.querySelectorAll(".tag-list li");

          var tags = [];
          tagEls2.forEach(function (el) {
            var t = sanitizeJdField(el.innerText, 60);
            if (t && tags.length < 30) tags.push(t);
          });

          // candidate_hash: content hash for duplicate detection.
          var contentForHash = [
            sanitizeJdField(titleEl ? titleEl.innerText : null, 200) || "",
            sanitizeJdField(salaryEl ? salaryEl.innerText : null, 100) || "",
          ].join("|");
          var candidateHash = await sha256Short(contentForHash);

          var candidate = {
            job_key: jobKey,
            rank: candidates.length + 1,
            title: sanitizeJdField(
              titleEl ? titleEl.innerText : null,
              200,
            ),
            company: sanitizeJdField(
              companyEl ? companyEl.innerText : null,
              200,
            ),
            salary: sanitizeJdField(
              salaryEl ? salaryEl.innerText : null,
              100,
            ),
            location: sanitizeJdField(
              areaEl ? areaEl.innerText : null,
              100,
            ),
            tags: tags.length > 0 ? tags : null,
            candidate_hash: candidateHash,
          };
          candidates.push(candidate);
          newCache[jobKey] = card;
        }

        // Update the in-memory cache. Cleared on URL change or new scan.
        lastScanCandidates = newCache;
        lastScanUrlHash = sha256ShortSync(window.location.href);

        result.job_candidates = candidates;
        result.count = candidates.length;
        return result;
      }

      if (ins.op === "open_job_by_key") {
        // Click one cached card ROOT only. Never click a.job-name,
        // a.more-job-btn, .op-btn-chat, .op-btn-like. Verify URL didn't
        // change after click (else unexpected_navigation hard-stop).
        clearScanCacheIfUrlChanged();

        var targetKey = ins.job_key;
        if (!targetKey) {
          result.success = false;
          result.error = sanitizeError("missing_job_key");
          return result;
        }
        var cardEl = lastScanCandidates[targetKey];
        if (!cardEl) {
          result.success = false;
          result.error = sanitizeError("candidate_not_found");
          return result;
        }

        // Safety: the matched element MUST be a div.job-card-wrap. If the
        // cache was corrupted and points to a different node, refuse.
        if (!cardEl.classList || !cardEl.classList.contains("job-card-wrap")) {
          result.success = false;
          result.error = sanitizeError("candidate_not_found");
          return result;
        }

        // Verify URL hasn't changed before clicking.
        var urlBefore = window.location.href;

        // Synthetic click on the card root. We do NOT click any descendant
        // anchor/button. The root click triggers BOSS's pane-switch JS
        // without opening a new tab or navigating.
        try {
          cardEl.click();
        } catch (clickErr) {
          result.success = false;
          result.error = sanitizeError("click_failed");
          return result;
        }

        // Brief microtask delay to let BOSS JS run.
        await new Promise(function (resolve) {
          setTimeout(resolve, 50);
        });

        // Verify URL did not change.
        if (window.location.href !== urlBefore) {
          result.success = false;
          result.error = sanitizeError("unexpected_navigation");
          return result;
        }

        result.url = await sha256Short(window.location.href);
        return result;
      }

      if (ins.op === "wait_job_detail_ready") {
        // Bounded readiness check: poll .job-detail-container .job-header-info
        // until its text contains expected_title. Never read .job-boss-info.
        var expectedTitle = ins.expected_title || "";
        if (!expectedTitle) {
          result.success = false;
          result.error = sanitizeError("missing_expected_title");
          return result;
        }

        var maxWaitMs = 5000;
        var pollIntervalMs = 200;
        var elapsed = 0;
        var ready = false;

        while (elapsed < maxWaitMs) {
          var pane2 = document.querySelector(".job-detail-container");
          if (pane2) {
            var headerInfo2 = pane2.querySelector(".job-header-info");
            if (headerInfo2 && typeof headerInfo2.innerText === "string") {
              var headerText = headerInfo2.innerText;
              if (headerText.includes(expectedTitle)) {
                ready = true;
                break;
              }
            }
          }
          await new Promise(function (resolve) {
            setTimeout(resolve, pollIntervalMs);
          });
          elapsed += pollIntervalMs;
        }

        if (!ready) {
          result.success = false;
          result.error = sanitizeError("detail_not_ready");
          return result;
        }
        result.success = true;
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
      if (ins.op === "probe_elements") {
        // Diagnostic op: return details about all matched elements so we can
        // find the correct send button in the BOSS chat panel. Returns up to 20
        // elements with tagName, className, id, aria-label, textContent preview,
        // and visibility. The CSS selector is in selector_value.
        const details = list.slice(0, 20).map((el) => ({
          tag: el.tagName,
          class: (el.className || "").toString().slice(0, 80),
          id: el.id || "",
          aria_label: el.getAttribute("aria-label") || "",
          text: (el.textContent || "").trim().slice(0, 80),
          visible: isElementVisible(el),
        }));
        // Bypass sanitizeText (120 char limit) — probe_elements needs more room.
        // The backend channel also sanitizes; we keep it reasonable at 8000 chars.
        result.text = JSON.stringify(details).slice(0, 8000);
        result.count = list.length;
        return result;
      }
      if (ins.op === "send_opening_message") {
        // Send the opening message in the chat dialog.
        // BOSS chat does NOT use a standard <button> for sending. The send
        // button is typically a <div>/<span> with an icon, and its class/name
        // varies across BOSS UI versions. The most reliable approach is to
        // simulate an Enter keypress in the textarea, which BOSS listens for
        // to send the message. If that fails (no textarea found), we fall back
        // to clicking any element matched by the CSS selector.
        //
        // The textarea selector is sent from the backend via
        // ins.extra_selectors.message_input (sourced from
        // COMMUNICATION_MESSAGE_INPUT in selectors.py) so the userscript does
        // not maintain its own copy. Fallback to a hardcoded selector for
        // backward compatibility with older backends.
        //
        // Strategy:
        //   1. Find the chat textarea, focus it, dispatch Enter keydown+keyup.
        //   2. If no textarea found, click the first visible matched element.
        const msgInputSel =
          (ins.extra_selectors && ins.extra_selectors.message_input) ||
          ".edit-area textarea, .chat-message textarea, .chat-input textarea, [class*='chat'] textarea";
        const textarea = document.querySelector(msgInputSel);
        if (textarea) {
          textarea.focus();
          // Dispatch a realistic Enter key sequence (keydown → keypress → keyup).
          // Some frameworks listen on keydown, others on keypress or keyup.
          const enterOpts = {
            key: "Enter",
            code: "Enter",
            keyCode: 13,
            which: 13,
            bubbles: true,
            cancelable: true,
          };
          textarea.dispatchEvent(new KeyboardEvent("keydown", enterOpts));
          textarea.dispatchEvent(new KeyboardEvent("keypress", enterOpts));
          textarea.dispatchEvent(new KeyboardEvent("keyup", enterOpts));
          result.visible = true;
          return result;
        }
        // Fallback: click the first VISIBLE element matched by the selector.
        if (list.length > 0) {
          let target = null;
          for (const el of list) {
            if (isElementVisible(el)) {
              target = el;
              break;
            }
          }
          if (!target) target = list[0];
          target.scrollIntoView({ block: "center", behavior: "instant" });
          target.click();
          result.visible = true;
          return result;
        }
        result.success = false;
        result.error = sanitizeError("element_not_found");
        return result;
      }
      if (ins.op === "read_communication_result") {
        // Count success / duplicate / error marker elements on the post-send
        // page. Return raw counts via result.marker_counts — the **backend**
        // classifies the result (see _classify_communication_markers in
        // userscript_adapter.py). The userscript does NOT decide the outcome.
        //
        // The 3 marker selector groups are sent from the backend via
        // ins.extra_selectors (sourced from COMMUNICATION_SUCCESS_MARKER,
        // COMMUNICATION_DUPLICATE_MARKER, PLATFORM_ERROR_MARKER in
        // selectors.py) so the userscript does not maintain its own copy.
        // Fallback to hardcoded selectors for backward compatibility with
        // older backends. We use querySelectorAllWithTextFilter instead of
        // native querySelectorAll because the selectors contain Playwright-only
        // ``:has-text()`` pseudo-selectors that would throw a DOMException in
        // native CSS.
        const extraSel = ins.extra_selectors || {};
        const successSel = extraSel.success ||
          ".chat-message:has-text('已发送'), .message-status:has-text('已发送'), .chat-content .message-item:not(.pending), .chat-message:has-text('发送成功'), [class*='message']:has-text('已发送')";
        const duplicateSel = extraSel.duplicate ||
          ".btn-start:has-text('继续沟通'), .chat-operate:has-text('继续沟通'), [class*='btn']:has-text('继续沟通'), [class*='operate']:has-text('继续沟通')";
        const errorSel = extraSel.error ||
          ".error-tip, .error-content, .upload-error";
        const successEls = querySelectorAllWithTextFilter(successSel);
        const duplicateEls = querySelectorAllWithTextFilter(duplicateSel);
        const errorEls = querySelectorAllWithTextFilter(errorSel);
        result.marker_counts = {
          success_count: successEls.length,
          duplicate_count: duplicateEls.length,
          error_count: errorEls.length,
        };
        return result;
      }
      if (ins.op === "scan_conversations") {
        // READ-ONLY scan of the BOSS chat list (Phase 1 feedback loop).
        // Observes which conversations got a reply / were read and returns
        // DESSENSITIZED entries only: sha256-hashed conversation keys plus
        // coarse status flags. Chat text, contact names, and message
        // previews are never read or sent — only fixed status labels
        // ("已读" / unread badges) are matched.
        const itemSelectors = [
          ".chat-item",
          ".conversation-item",
          "[class*='chat-item']",
          "[class*='conversation-item']",
        ];
        let items = [];
        for (const sel of itemSelectors) {
          try {
            items = Array.from(document.querySelectorAll(sel));
          } catch (_e) {
            items = [];
          }
          if (items.length > 0) break;
        }
        if (items.length === 0) {
          result.success = false;
          result.error = sanitizeError("chat_list_not_found");
          return result;
        }
        const conversations = [];
        for (const item of items.slice(0, 50)) {
          // Conversation key: prefer a stable data attribute / link href.
          // Never fall back to visible text (that would leak contact names).
          const link = item.querySelector ? item.querySelector("a[href]") : null;
          const key =
            item.getAttribute("data-uid") ||
            item.getAttribute("data-id") ||
            item.id ||
            (link ? link.getAttribute("href") : null);
          if (!key) continue;
          // Status classification from fixed labels / badges only.
          let status = "unknown";
          const hasUnreadBadge =
            item.querySelector(".badge, [class*='badge'], [class*='unread']") !== null;
          let labelText = "";
          const statusEl = item.querySelector(
            "[class*='status'], [class*='time'], [class*='read']"
          );
          if (statusEl && typeof statusEl.textContent === "string") {
            // Fixed status label only — capped and never forwarded raw.
            labelText = statusEl.textContent.trim().slice(0, 20);
          }
          if (hasUnreadBadge) {
            // An unread badge means the other side sent a new message.
            status = "replied";
          } else if (labelText.includes("已读")) {
            status = "read";
          } else if (labelText.includes("未读")) {
            status = "replied";
          }
          conversations.push({
            conversation_key_hash: await sha256Short(key),
            status: status,
          });
        }
        result.conversations = conversations;
        result.count = conversations.length;
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
    const headers = options.headers || { "Content-Type": "application/json" };
    if (BRIDGE_TOKEN) {
      headers["X-Bridge-Token"] = BRIDGE_TOKEN;
    }
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: options.method || "GET",
        url: url,
        headers: headers,
        data: options.body ? JSON.stringify(options.body) : undefined,
        timeout: options.timeout || 15000,
        onload(resp) {
          if (resp.status === 204) {
            resolve(null);
          } else if (resp.status >= 200 && resp.status < 300) {
            try {
              resolve(resp.responseText ? JSON.parse(resp.responseText) : {});
            } catch (_e) {
              resolve({});
            }
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
