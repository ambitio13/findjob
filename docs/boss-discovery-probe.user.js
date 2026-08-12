// ==UserScript==
// @name         BOSS Discovery Probe (诊断专用)
// @namespace    tou-jianli-agent
// @version      0.1.0
// @description  只读诊断 BOSS 推荐列表/职位详情页 DOM 结构，为 discovery 流水线取证。不点击、不导航、不上传，报告渲染在本页浮层里手动复制。
// @match        https://www.zhipin.com/*
// @grant        GM_setClipboard
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  // ---------- helpers ----------
  const short = (s, n = 80) => (s || "").trim().replace(/\s+/g, " ").slice(0, n);

  const count = (sel) => {
    try {
      return document.querySelectorAll(sel).length;
    } catch (e) {
      return -1; // selector invalid
    }
  };

  const firstClasses = (sel, limit = 3) =>
    Array.from(document.querySelectorAll(sel))
      .slice(0, limit)
      .map((el) => short(el.className && el.className.baseVal !== undefined ? "(svg)" : el.className, 120));

  // ---------- page type detection ----------
  function detectPageType() {
    const p = location.pathname;
    if (/job-recommend|recommend/.test(p)) return "recommend_list";
    if (/job_detail/.test(p)) return "job_detail";
    if (/jobs/.test(p)) return "search_or_jobs";
    return "other:" + p;
  }

  // ---------- list page probe ----------
  function probeListPage() {
    // Candidate card container selectors — ordered guesses, counts tell the truth.
    const cardCandidates = [
      ".job-card-wrapper",
      ".job-card-body",
      "[class*=job-card]",
      "[class*=jobCard]",
      ".recommend-job-card",
      ".job-list-box li",
      ".search-job-result .job-list li",
      "li[ka]",
    ];
    const cardCounts = Object.fromEntries(cardCandidates.map((s) => [s, count(s)]));

    // Anchors pointing at job detail — the key evidence for open behavior.
    const anchors = Array.from(document.querySelectorAll("a[href]")).filter((a) =>
      a.href.includes("/job_detail/")
    );
    const targetStats = {
      total: anchors.length,
      target_blank: anchors.filter((a) => a.target === "_blank").length,
      target_self_or_empty: anchors.filter((a) => !a.target || a.target === "_self").length,
    };

    // How are anchors nested? card root class sampling.
    const anchorParentClasses = anchors.slice(0, 5).map((a) => {
      let el = a;
      const chain = [];
      for (let i = 0; i < 3 && el; i += 1) {
        chain.push(`${el.tagName.toLowerCase()}.${short(el.className, 40)}`);
        el = el.parentElement;
      }
      return chain.join(" < ");
    });

    // Field selector candidates inside the first card.
    const fieldCandidates = {
      title: [".job-name", ".job-title", "[class*=job-name]", "[class*=title]"],
      company: [".company-name a", ".company-name", "[class*=company-name]", "[class*=company]"],
      salary: [".salary", ".job-salary", "[class*=salary]"],
      location: [".job-area", "[class*=job-area]", "[class*=area]"],
      tags: [".tag-list li", ".job-tags .tag", "[class*=tag]"],
      communicated_badge: ["[class*=communicate]", "[class*=continue-chat]"],
    };
    const fieldCounts = {};
    for (const [name, sels] of Object.entries(fieldCandidates)) {
      fieldCounts[name] = Object.fromEntries(sels.map((s) => [s, count(s)]));
    }

    // Badge text evidence (only presence, no personal names).
    const bodyText = document.body.innerText || "";
    const badges = {
      has_continue_chat: bodyText.includes("继续沟通"),
      has_chatted: bodyText.includes("已沟通"),
    };

    // One card sample: structure only + truncated public job text (no names/links).
    const firstCard =
      document.querySelector(".job-card-wrapper") || document.querySelector("[class*=job-card]");
    let cardSample = null;
    if (firstCard) {
      cardSample = {
        rootTag: firstCard.tagName.toLowerCase(),
        rootClass: short(firstCard.className, 120),
        innerAnchor: firstCard.querySelector('a[href*="/job_detail/"]')
          ? {
              target: firstCard.querySelector('a[href*="/job_detail/"]').target || "(none)",
              hasHref: true,
            }
          : null,
        textSample: short(firstCard.innerText, 200),
      };
    }

    // Active (selected) card evidence for the master-detail pane.
    const activeCard = document.querySelector(".job-card-wrap.active, [class*=job-card].active");

    return {
      page_type: "recommend_list_or_jobs",
      pathname: location.pathname,
      card_counts: cardCounts,
      active_card: activeCard
        ? { rootClass: short(activeCard.className, 120) }
        : null,
      list_pane: probeListPane(),
      anchor_target_stats: targetStats,
      anchor_parent_chain: anchorParentClasses,
      field_selector_counts: fieldCounts,
      badges,
      first_card_sample: cardSample,
    };
  }

  // ---------- inline detail pane probe (master-detail layout) ----------
  function probeListPane() {
    // Locate the deepest element containing the JD section header.
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    let target = null;
    while ((node = walker.nextNode())) {
      if ((node.textContent || "").includes("职位描述")) {
        target = node.parentElement;
        break;
      }
    }
    if (!target) return { pane_found: false };

    // Climb to the LARGEST container that still excludes the card list,
    // i.e. the pane itself (stop right before an ancestor that contains cards).
    let container = target;
    while (
      container.parentElement &&
      !container.parentElement.querySelector(".job-card-wrap")
    ) {
      container = container.parentElement;
    }

    const chain = [];
    let el = container;
    for (let i = 0; i < 5 && el; i += 1) {
      chain.push(`${el.tagName.toLowerCase()}.${short(el.className, 60)}`);
      el = el.parentElement;
    }
    const q = (sel) => {
      try {
        return container.querySelectorAll(sel).length;
      } catch (e) {
        return -1;
      }
    };
    const kids = (nodeEl, depth) =>
      depth > 2
        ? []
        : Array.from(nodeEl.children)
            .slice(0, 8)
            .map((k) => ({
              sel: `${k.tagName.toLowerCase()}.${short(k.className, 50)}`,
              textLen: (k.innerText || "").length,
              kids: kids(k, depth + 1),
            }));
    const titleEl = container.querySelector("h3, h1");
    return {
      pane_found: true,
      pane_chain: chain,
      counts: {
        h3: q("h3"),
        "[class*=salary]": q("[class*=salary]"),
        "[class*=company]": q("[class*=company]"),
        "[class*=location]": q("[class*=location]"),
        "[class*=detail]": q("[class*=detail]"),
        "[class*=text]": q("[class*=text]"),
        "[class*=desc]": q("[class*=desc]"),
        "[class*=tag]": q("[class*=tag]"),
        "button, a.btn, [class*=btn]": q("button, a.btn, [class*=btn]"),
      },
      buttons: Array.from(container.querySelectorAll("button, [class*=btn]"))
        .slice(0, 6)
        .map((b) => ({
          cls: short(b.className, 50),
          text: short(b.innerText, 20),
        })),
      tree: kids(container, 0),
      pane_title_sample: titleEl ? short(titleEl.innerText, 60) : null,
    };
  }

  // ---------- detail page probe ----------
  function probeDetailPage() {
    const fieldCandidates = {
      title: [".name h1", ".job-banner .name", "h1", "[class*=job-title]"],
      company: [".company-info a", ".sider-company .name", "[class*=company] .name"],
      salary: [".salary", ".job-banner .salary", "[class*=salary]"],
      description: [".job-detail", ".job-sec-text", ".text", "[class*=detail]"],
      location: [".location-address", ".job-detail .location", "[class*=location]"],
    };
    const fieldCounts = {};
    for (const [name, sels] of Object.entries(fieldCandidates)) {
      fieldCounts[name] = Object.fromEntries(sels.map((s) => [s, count(s)]));
    }
    return {
      page_type: "job_detail",
      pathname: location.pathname,
      field_selector_counts: fieldCounts,
    };
  }

  // ---------- render floating panel ----------
  function renderPanel(report) {
    const json = JSON.stringify(report, null, 2);
    const panel = document.createElement("div");
    panel.style.cssText =
      "position:fixed;right:16px;bottom:16px;z-index:2147483647;width:420px;max-height:60vh;" +
      "overflow:auto;background:#111;color:#0f0;font:12px/1.5 monospace;padding:12px;" +
      "border-radius:8px;box-shadow:0 4px 24px rgba(0,0,0,.4);";
    const pre = document.createElement("pre");
    pre.style.cssText = "white-space:pre-wrap;word-break:break-all;margin:8px 0 0;";
    pre.textContent = json;

    const btnRow = document.createElement("div");
    const copyBtn = document.createElement("button");
    copyBtn.textContent = "复制报告";
    copyBtn.style.cssText = "margin-right:8px;padding:4px 10px;cursor:pointer;";
    copyBtn.addEventListener("click", () => {
      try {
        GM_setClipboard(json);
        copyBtn.textContent = "已复制 ✓";
      } catch (e) {
        copyBtn.textContent = "复制失败，请手动全选";
      }
      setTimeout(() => (copyBtn.textContent = "复制报告"), 2000);
    });
    const closeBtn = document.createElement("button");
    closeBtn.textContent = "关闭";
    closeBtn.style.cssText = "padding:4px 10px;cursor:pointer;";
    closeBtn.addEventListener("click", () => panel.remove());
    btnRow.append(copyBtn, closeBtn);

    const header = document.createElement("div");
    header.textContent = "Discovery Probe — " + new Date().toLocaleTimeString();

    panel.append(header, btnRow, pre);
    document.body.appendChild(panel);
  }

  // ---------- entry ----------
  function run() {
    const type = detectPageType();
    let report;
    if (type === "job_detail") {
      report = probeDetailPage();
    } else {
      report = probeListPage();
    }
    report.probe_version = "0.1.0";
    report.detected_page_type = type;
    report.collected_at = new Date().toISOString();
    renderPanel(report);
  }

  // Small floating trigger button so the user decides when to probe (read-only).
  const trigger = document.createElement("button");
  trigger.textContent = "🔍 Probe";
  trigger.style.cssText =
    "position:fixed;right:16px;bottom:16px;z-index:2147483647;padding:8px 14px;" +
    "border-radius:20px;border:none;background:#00a6a6;color:#fff;font-size:14px;cursor:pointer;";
  trigger.addEventListener("click", run);
  document.body.appendChild(trigger);
})();
