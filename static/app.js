(() => {
  "use strict";

  const state = {
    projects: [],
    projectId: null,
    files: [],
    resultsByProject: {},
    narrativeByProject: {},
    contentOpen: false,
    logsOpen: false,
    selectedLogId: null,
    selectedLogName: null,
    logsLastActivePath: null,
    requestToken: 0,
    lastSearch: { query: "", index: 0 },
    openaiConfigured: false,
  };

  const $ = (id) => document.getElementById(id);
  const nav = $("projectNav");
  const dropZone = $("dropZone");
  const fileInput = $("fileInput");
  const fileList = $("fileList");
  const results = $("results");

  // -------------------- Theme (light / auto / dark) --------------------
  const themeButtons = Array.from(document.querySelectorAll(".theme-seg button[data-theme-set]"));
  function applyTheme(choice) {
    let effective = choice;
    if (choice === "auto") {
      effective = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
        ? "light" : "dark";
    }
    document.documentElement.setAttribute("data-theme", effective);
    document.documentElement.setAttribute("data-theme-choice", choice);
    themeButtons.forEach((b) => b.classList.toggle("active", b.dataset.themeSet === choice));
  }
  function setTheme(choice) {
    localStorage.setItem("autobot_theme", choice);
    applyTheme(choice);
  }
  themeButtons.forEach((b) => b.addEventListener("click", () => setTheme(b.dataset.themeSet)));
  const savedTheme = localStorage.getItem("autobot_theme") || "auto";
  applyTheme(savedTheme);
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
      const current = localStorage.getItem("autobot_theme") || "auto";
      if (current === "auto") applyTheme("auto");
    });
  }

  // -------------------- Server runtime configuration --------------------
  localStorage.removeItem("autobot_openai_key");
  async function loadRuntimeConfig() {
    try {
      const res = await fetch("/api/config", { cache: "no-store" });
      if (!res.ok) throw new Error(`Config check failed (${res.status})`);
      const config = await res.json();
      state.openaiConfigured = Boolean(config.openai_configured);
      $("keyState").classList.toggle("on", state.openaiConfigured);
      $("keyStatus").textContent = state.openaiConfigured
        ? "OpenAI server key configured"
        : "OpenAI server key missing";
      $("openaiModel").textContent = config.openai_model || "gpt-5.6-sol";
      $("useOpenai").disabled = !state.openaiConfigured;
      $("narrateBtn").disabled = !state.openaiConfigured;
    } catch (error) {
      $("keyStatus").textContent = "OpenAI status unavailable";
      $("useOpenai").disabled = true;
      $("narrateBtn").disabled = true;
    }
  }

  // -------------------- Banner --------------------
  function showBanner(kind, message, detail = null) {
    const banner = $("banner");
    banner.hidden = false;
    banner.className = `banner ${kind}`;
    const icons = { ok: "✓", error: "!", warn: "⚠", info: "i" };
    $("bannerIcon").textContent = icons[kind] || "i";
    $("bannerMessage").textContent = message;
    const wrap = $("bannerDetailsWrap");
    if (detail) {
      wrap.hidden = false;
      $("bannerDetails").textContent = detail;
    } else {
      wrap.hidden = true;
    }
  }
  function hideBanner() { $("banner").hidden = true; }
  $("bannerClose").onclick = hideBanner;

  // -------------------- Helpers --------------------
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  function relativeTime(iso) {
    if (!iso) return null;
    const then = new Date(iso.replace(" ", "T"));
    if (isNaN(then)) return null;
    const diff = (Date.now() - then.getTime()) / 1000;
    if (diff < 45) return "just now";
    if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
    if (diff < 86400 * 7) return `${Math.round(diff / 86400)}d ago`;
    return then.toLocaleDateString();
  }
  function freshness(iso) {
    if (!iso) return "stale";
    const then = new Date(iso.replace(" ", "T"));
    if (isNaN(then)) return "stale";
    const diff = (Date.now() - then.getTime()) / 1000;
    return diff < 3600 * 6 ? "fresh" : "stale";
  }
  async function apiFetch(url, options = {}, retryOnce = true) {
    try { return await fetch(url, options); }
    catch (error) {
      if (!retryOnce) throw error;
      await new Promise((r) => setTimeout(r, 600));
      return fetch(url, options);
    }
  }
  async function readJson(res) {
    const text = await res.text();
    try { return JSON.parse(text); }
    catch {
      const snippet = text.replace(/\s+/g, " ").trim().slice(0, 180) || `(HTTP ${res.status})`;
      throw new Error(snippet);
    }
  }
  async function gzipFile(file) {
    if (typeof CompressionStream !== "function") return file;
    const stream = file.stream().pipeThrough(new CompressionStream("gzip"));
    const blob = await new Response(stream).blob();
    return new File([blob], `${file.name}.gz`, { type: "application/gzip" });
  }
  function setBusy(button, busy, labelWhileBusy = null) {
    if (!button) return;
    if (busy) {
      button.classList.add("busy");
      button.disabled = true;
      const label = button.querySelector(".btn-label");
      if (label && labelWhileBusy) {
        button.dataset.origLabel = button.dataset.origLabel || label.textContent;
        label.textContent = labelWhileBusy;
      }
    } else {
      button.classList.remove("busy");
      button.disabled = false;
      const label = button.querySelector(".btn-label");
      if (label && button.dataset.origLabel) {
        label.textContent = button.dataset.origLabel;
        delete button.dataset.origLabel;
      }
    }
  }

  // -------------------- Project navigation --------------------
  function currentProject() {
    return state.projects.find((p) => p.id === state.projectId);
  }

  function renderNav() {
    nav.innerHTML = "";
    state.projects.forEach((p) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = p.id === state.projectId ? "active" : "";
      const analyzed = (p.state && p.state.last_analyzed_at) || null;
      const rel = analyzed ? relativeTime(analyzed) : "not analyzed yet";
      const cls = analyzed ? freshness(analyzed) : "stale";
      b.innerHTML = `
        <span class="short">${escapeHtml(p.short)}</span>
        <span class="name">${escapeHtml(p.name)}</span>
        <span class="nav-time ${cls}">last: ${escapeHtml(rel)}</span>`;
      b.onclick = () => selectProject(p.id);
      nav.appendChild(b);
    });
  }

  async function refreshProjectState(projectId) {
    try {
      const res = await apiFetch(`/api/projects/${encodeURIComponent(projectId)}/state`, { cache: "no-store" });
      if (!res.ok) return;
      const s = await res.json();
      const p = state.projects.find((x) => x.id === projectId);
      if (p) { p.state = s; renderNav(); renderTopMeta(); }
    } catch { /* ignore */ }
  }

  function renderTopMeta() {
    const p = currentProject();
    if (!p) { $("projMeta").textContent = ""; return; }
    const analyzed = p.state && p.state.last_analyzed_at;
    const pushed = p.state && p.state.last_sheet_push_at;
    const parts = [];
    if (analyzed) parts.push(`Last analyzed · ${relativeTime(analyzed)}`);
    else parts.push("Not analyzed yet");
    if (p.sheet_push_available) {
      parts.push(pushed ? `Sheet updated · ${relativeTime(pushed)}` : "Sheet never updated from here");
    }
    $("projMeta").innerHTML = parts.map((x) => escapeHtml(x)).join("<br/>");
  }

  function selectProject(id) {
    state.requestToken += 1;
    state.projectId = id;
    state.files = [];
    state.selectedLogId = null;
    state.selectedLogName = null;
    fileList.textContent = "";
    fileInput.value = "";
    $("analyzeBtn").disabled = true;
    $("analyzePushBtn").disabled = true;
    renderNav();

    const p = currentProject();
    if (!p) return;
    document.title = `${p.short} · Autobot Log Hub`;
    $("projTitle").textContent = p.name;
    $("projDesc").textContent = p.description;
    fileInput.multiple = !!p.multi_file;
    fileInput.accept = p.accept || ".log,.txt";
    $("dropHint").textContent = p.multi_file
      ? "Drop strategy1.log, strategy2.log, and strategy3.log"
      : `Drop ${p.short} log · or click to browse`;
    $("dropExtras").textContent = p.multi_file
      ? "Three files supported. SuperMA KR V2-1/V2-2/V2-3 are auto-detected."
      : "";
    dropZone.classList.remove("compact");
    renderTopMeta();

    const projectSheet = $("projectSheetBtn");
    if (p.sheet_url) {
      projectSheet.href = p.sheet_url;
      projectSheet.classList.remove("disabled");
      projectSheet.removeAttribute("aria-disabled");
      projectSheet.innerHTML = '<span class="btn-icon">↗</span> Open Sheet';
      $("repushBtn").hidden = !p.sheet_push_available;
      $("analyzePushBtn").hidden = !p.sheet_push_available;
    } else {
      projectSheet.removeAttribute("href");
      projectSheet.classList.add("disabled");
      projectSheet.setAttribute("aria-disabled", "true");
      projectSheet.innerHTML = '<span class="btn-icon">—</span> No Sheet';
      $("repushBtn").hidden = true;
      $("analyzePushBtn").hidden = true;
    }

    hideBanner();
    if (state.resultsByProject[id]) {
      renderResult({ project_id: id, result: state.resultsByProject[id], narrative: state.narrativeByProject[id] || null });
      dropZone.classList.add("compact");
    } else {
      clearResults();
    }
    if (state.contentOpen) { $("contentPanel").hidden = false; loadContentList(id); }
    else $("contentPanel").hidden = true;
    if (state.logsOpen) { $("logsPanel").hidden = false; loadLogFiles(id); }
    else $("logsPanel").hidden = true;
  }

  function clearResults() {
    results.hidden = true;
    $("summaryCards").innerHTML = "";
    $("analysisBody").innerHTML = "";
    $("analysisMeta").textContent = "";
    $("narrativeBody").textContent = "— (configure OPENAI_API_KEY and click Generate)";
    $("narrativeMeta").textContent = "";
    $("chartPanel").hidden = true;
  }

  // -------------------- File drop --------------------
  function onFiles(fileListLike) {
    const p = currentProject();
    if (!p) return;
    let arr = Array.from(fileListLike || []);
    if (!p.multi_file) arr = arr.slice(0, 1);
    state.files = arr;
    fileList.textContent = arr.map((f) => `${f.name} · ${formatBytes(f.size)}`).join(" · ");
    $("analyzeBtn").disabled = arr.length === 0;
    $("analyzePushBtn").disabled = arr.length === 0 || !p.sheet_push_available;
  }
  dropZone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => onFiles(fileInput.files));
  ["dragenter", "dragover"].forEach((ev) => {
    dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.add("drag"); });
  });
  ["dragleave", "drop"].forEach((ev) => {
    dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.remove("drag"); });
  });
  dropZone.addEventListener("drop", (e) => onFiles(e.dataTransfer.files));

  // -------------------- Rendering --------------------
  function card(label, value, cls = "", tip = "") {
    const info = tip ? `<span class="info-tip" title="${escapeHtml(tip)}">?</span>` : "";
    return `<div class="card"><div class="k">${escapeHtml(label)}${info}</div><div class="v ${cls}">${value}</div></div>`;
  }

  const TIPS = {
    hold_return: "Return since the first tick with an open position, using cash-flow adjusted equity (excludes deposits and margin deploy cliffs).",
    mdd: "Deepest peak-to-trough drawdown while positions are open.",
    peak: "Maximum capital deployed into positions at any single moment.",
    total_ret: "Total P&L divided by peak-invested capital (not full account equity).",
    total_pnl: "Sum of realized + unrealized P&L.",
    n_ticks: "Live-mode ticks parsed from the log (DRY_RUN=False).",
    buys_sells: "Successful buy / sell fills over the whole log window.",
  };

  function renderCoin(r) {
    const s = r.summary || {};
    $("summaryCards").innerHTML = [
      card("Hold return", `${s.hold_ret_pct}%`, s.hold_ret_pct >= 0 ? "pos" : "neg", TIPS.hold_return),
      card("MDD", `${s.mdd_pct}%`, "neg", TIPS.mdd),
      card("Adj equity", s.latest_adj_equity, "", "Latest equity after cash-flow adjustments."),
      card("Positions", s.n_pos, "", "Open positions at the latest tick."),
      card("Ticks", s.n_ticks, "", TIPS.n_ticks),
    ].join("");

    const events = s.cash_events || [];
    const adj = events.length
      ? `<div class="section-title">Cash-flow adjustments</div>
         <div class="adj-list">${events.map((e) => `
           <div class="adj-item ${escapeHtml(e.kind)}">
             <span><span class="adj-tag">${escapeHtml(e.kind)}</span> · ${escapeHtml(e.utc)}${e.n_pos ? " · " + escapeHtml(e.n_pos) : ""}</span>
             <span class="hl">${escapeHtml(String(e.amount))}</span>
           </div>`).join("")}</div>`
      : `<div class="section-title">Cash-flow adjustments</div><div class="muted tiny">없음</div>`;

    const notes = (r.trade_notes || []).map((n) => `<div>• ${escapeHtml(n)}</div>`).join("");
    $("analysisBody").innerHTML = `
      <table class="kv">
        <tr><th>구간 (UTC)</th><td>${escapeHtml(s.range_utc || "")}</td></tr>
        <tr><th>포지션</th><td>${escapeHtml((s.positions || []).join(", "))}</td></tr>
        <tr><th>MDD 시점</th><td>${escapeHtml(s.mdd_at || "")} · 저점 ${escapeHtml(String(s.mdd_eq))}</td></tr>
        <tr><th>저장 위치</th><td><code>${escapeHtml(r.saved_to || "")}</code></td></tr>
      </table>
      ${adj}
      <div class="section-title">최근 이벤트 (${(r.trade_notes || []).length})</div>
      <div class="muted">${notes || "없음"}</div>`;
    $("analysisMeta").textContent = `${(r.series || []).length} chart points`;
    drawSeries(r.series || []);
  }

  function renderSmallcap(r) {
    const s = r.summary || {};
    $("summaryCards").innerHTML = [
      card("수익률%", s.total_ret_pct, s.total_ret_pct >= 0 ? "pos" : "neg", TIPS.total_ret),
      card("MDD%", s.mdd_pct, "neg", TIPS.mdd),
      card("총손익", s.total_pnl, "", TIPS.total_pnl),
      card("피크투입", s.peak_invested, "", TIPS.peak),
      card("보유", s.open_positions, "", "Number of currently held tickers."),
      card("매수/매도", `${r.n_buys}/${r.n_sells}`, "", TIPS.buys_sells),
    ].join("");
    const shown = Math.min(15, (r.positions || []).length);
    const total = (r.positions || []).length;
    const posRows = (r.positions || []).slice(0, 15).map((p) =>
      `<tr><th>${escapeHtml(p.name || p.code)}</th><td>${escapeHtml(String(p.qty))} @ ${escapeHtml(String(p.entry_px || p.avg_px || ""))}</td></tr>`
    ).join("");
    $("analysisBody").innerHTML = `
      <table class="kv">
        <tr><th>기준일</th><td>${escapeHtml(s.asof || "")}</td></tr>
        <tr><th>시작일</th><td>${escapeHtml(s.start || "")}</td></tr>
        <tr><th>리포트 폴더</th><td><code>${escapeHtml(r.report_dir || "")}</code></td></tr>
      </table>
      <div class="section-title">Positions</div>
      <div class="table-cap"><span>Showing ${shown} of ${total}</span></div>
      <table class="kv">${posRows || "<tr><td>없음</td></tr>"}</table>`;
    $("analysisMeta").textContent = `${r.n_fills} fills · ${total} open`;
    $("chartPanel").hidden = true;
  }

  function renderSuperma(r) {
    $("summaryCards").innerHTML = (r.bots || [])
      .map((b) => card(b.bot, `${b.day} ${b.time}`, b.healthy ? "pos" : "neg", "Latest session for this bot."))
      .join("");
    const blocks = (r.bots || []).map((b) => {
      const rows = Object.entries(b.table || {}).map(([k, v]) => {
        const cls = k === "에러" && v !== "없음" ? "bad" : k === "리밸" && String(v).includes("**") ? "log-rebalance-nz" : "";
        return `<tr><th>${escapeHtml(k)}</th><td class="${cls}">${escapeHtml(String(v)).replace(/\*\*(-?\d+)\*\*/g, "<strong>$1</strong>")}</td></tr>`;
      }).join("");
      return `<div class="section-title">${escapeHtml(b.bot)} — ${escapeHtml(b.time)}</div>
        <table class="kv">${rows}</table>
        <p class="muted tiny" style="margin-top:6px">${escapeHtml(b.one_liner)}</p>`;
    }).join("");
    $("analysisBody").innerHTML = blocks +
      `<div class="section-title">한줄 결론</div><div>${escapeHtml(r.one_liner || "")}</div>`;
    $("analysisMeta").textContent = `${(r.bots || []).length} bot(s)`;
    $("chartPanel").hidden = true;
  }

  function fmtNum(v) {
    if (v == null || v === "") return "";
    const n = Number(v);
    if (!Number.isFinite(n)) return String(v);
    return n.toLocaleString("ko-KR");
  }

  function renderBollinger(r) {
    const s = r.summary || {};
    $("summaryCards").innerHTML = [
      card("수익률%", s.total_return_pct, s.total_return_pct >= 0 ? "pos" : "neg", TIPS.total_ret),
      card("MDD%", s.mdd_pct, "neg", TIPS.mdd),
      card("CAGR%", s.cagr_pct, s.cagr_pct >= 0 ? "pos" : "neg", "Annualized return over the live window."),
      card("Sharpe", s.sharpe, "", "Daily-return Sharpe annualized at 252 days."),
      card("최종자산", fmtNum(s.final_equity), "", "Last TotalMoney after capital-reset trim."),
      card("매수/매도", `${r.n_buys || 0}/${r.n_sells || 0}`, "", TIPS.buys_sells),
    ].join("");
    const trades = (r.trades || []).slice(-15).map((t) =>
      `<tr><th>${escapeHtml(t.date)} ${escapeHtml(t.side)}</th><td>${escapeHtml(t.code)} ${t.shares != null ? "x" + escapeHtml(String(t.shares)) : ""} @ ${fmtNum(t.price)} ${escapeHtml(t.reason || "")}</td></tr>`
    ).join("");
    $("analysisBody").innerHTML = `
      <table class="kv">
        <tr><th>구간</th><td>${escapeHtml(s.start_date || "")} ~ ${escapeHtml(s.end_date || "")} (${escapeHtml(String(s.trading_days || ""))}거래일)</td></tr>
        <tr><th>초기→최종</th><td>${fmtNum(s.initial_equity)} → ${fmtNum(s.final_equity)}</td></tr>
        <tr><th>MDD 발생일</th><td>${escapeHtml(s.mdd_date || "")}</td></tr>
        <tr><th>보유 슬롯</th><td>${escapeHtml(String(s.latest_positions ?? ""))}/5</td></tr>
        <tr><th>예약매수</th><td>${escapeHtml((r.pending_buys || []).join(", ") || "없음")}</td></tr>
        <tr><th>자본 보정</th><td>${escapeHtml(s.capital_note || "로그 전체")}</td></tr>
        <tr><th>저장 위치</th><td><code>${escapeHtml(r.saved_to || "")}</code></td></tr>
      </table>
      <div class="section-title">최근 체결 (${(r.trades || []).length})</div>
      <table class="kv">${trades || "<tr><td>없음</td></tr>"}</table>`;
    $("analysisMeta").textContent = `${(r.series || []).length} chart points · ${s.trading_days || 0} days`;
    drawSeries(r.series || []);
  }

  function renderStrategyD(r) {
    const s = r.summary || {};
    $("summaryCards").innerHTML = [
      card("TWR%", s.twr_pct, s.twr_pct >= 0 ? "pos" : "neg", "Time-weighted return after deposit/withdrawal breaks."),
      card("MDD%", s.mdd_pct, "neg", TIPS.mdd),
      card("PnL USDT", s.pnl_usdt, s.pnl_usdt >= 0 ? "pos" : "neg", "Estimated equity minus invested capital."),
      card("XIRR%", s.xirr_pct == null ? "n/a" : s.xirr_pct, "", "Annualized IRR including cash flows."),
      card("추정자산", fmtNum(s.equity_est), "", "Latest estimated account equity."),
      card("포지션", s.n_pos, "", "Open Strategy D names at the latest tick."),
    ].join("");
    const events = s.cash_events || [];
    const adj = events.length
      ? `<div class="section-title">Cash-flow adjustments</div>
         <div class="adj-list">${events.map((e) => `
           <div class="adj-item ${escapeHtml(e.kind)}">
             <span><span class="adj-tag">${escapeHtml(e.kind)}</span> · ${escapeHtml(e.utc)}${e.n_pos ? " · " + escapeHtml(e.n_pos) : ""}</span>
             <span class="hl">${escapeHtml(String(e.amount))}</span>
           </div>`).join("")}</div>`
      : `<div class="section-title">Cash-flow adjustments</div><div class="muted tiny">없음</div>`;
    const notes = (r.trade_notes || []).map((n) => `<div>• ${escapeHtml(n)}</div>`).join("");
    $("analysisBody").innerHTML = `
      <table class="kv">
        <tr><th>구간 (UTC)</th><td>${escapeHtml(s.range_utc || "")}</td></tr>
        <tr><th>원금</th><td>${fmtNum(s.capital_in)}</td></tr>
        <tr><th>원금대비 ROI</th><td>${escapeHtml(String(s.roi_pct ?? ""))}%</td></tr>
        <tr><th>MDD 시점</th><td>${escapeHtml(s.mdd_at || "")} · ${fmtNum(s.mdd_eq)}</td></tr>
        <tr><th>포지션</th><td>${escapeHtml((s.positions || []).join(", ") || "없음")}</td></tr>
        <tr><th>USDT free</th><td>${fmtNum(s.free)}</td></tr>
        <tr><th>저장 위치</th><td><code>${escapeHtml(r.saved_to || "")}</code></td></tr>
      </table>
      ${adj}
      <div class="section-title">최근 이벤트 (${(r.trade_notes || []).length})</div>
      <div class="muted">${notes || "없음"}</div>`;
    $("analysisMeta").textContent = `${s.n_ticks || 0} ticks · ${(r.series || []).length} chart points`;
    drawSeries(r.series || []);
  }

  function renderAssetAlloc(r) {
    const s = r.summary || {};
    if (s.mode === "calendar") {
      $("summaryCards").innerHTML = [
        card("모드", "달력", "", "월말 리밸런싱 대상일 체크 로그만 있습니다."),
        card("마지막 대상일", s.last_ok_day || "—", "", "last weekday of month."),
        card("skip", s.skip_count || 0, "", "대상일이 아니라 스킵된 횟수."),
      ].join("");
      $("analysisBody").innerHTML = `<div>${escapeHtml(s.one_liner || "")}</div>
        <p class="muted tiny">KIS_AssetAll_Bot.log 를 올리면 총자산·리밸 세션을 분석합니다.</p>`;
      $("analysisMeta").textContent = "calendar log";
      $("chartPanel").hidden = true;
      return;
    }
    const sleeves = s.sleeves || {};
    $("summaryCards").innerHTML = [
      card("총자산", fmtNum(s.total_money), "", "KIS Remain+Stock TotalMoney."),
      card("할당금액", fmtNum(s.allocation), "", "3전략 분할 포트폴리오 할당."),
      card("주식평가", fmtNum(s.stock_money), s.stock_revenue >= 0 ? "pos" : "neg", "보유 ETF 평가합."),
      card("평가손익", fmtNum(s.stock_revenue), s.stock_revenue >= 0 ? "pos" : "neg", "보유 평가손익."),
      card("보유", s.n_holdings || 0, "", "내 보유 주식 종목 수."),
      card("리밸/주문", `${s.n_rebalance || 0}/${s.n_orders || 0}`, s.healthy ? "pos" : "neg", "비영 리밸 종목 / 실제 OrderNum."),
    ].join("");
    const holdRows = (r.holdings || []).map((h) =>
      `<tr><th>${escapeHtml(h.name)} (${escapeHtml(h.code)})</th><td>${fmtNum(h.qty)} · ${fmtNum(h.mkt)} · ${escapeHtml(String(h.ret_pct))}%</td></tr>`
    ).join("");
    const rebalRows = (r.rebalance || []).map((row) => {
      const qty = Number(row.qty || 0);
      const cls = qty ? "log-rebalance-nz" : "";
      return `<tr><th>${escapeHtml(row.name)} (${escapeHtml(row.code)})</th><td class="${cls}">${qty >= 0 ? "+" : ""}${fmtNum(qty)} · ${escapeHtml(String(row.now_pct ?? ""))}→${escapeHtml(String(row.target_pct ?? ""))}%</td></tr>`;
    }).join("");
    const comb = Object.entries(s.combined || {}).map(([k, v]) => `${k} ${(Number(v) * 100).toFixed(1)}%`).join(" · ");
    $("analysisBody").innerHTML = `
      <table class="kv">
        <tr><th>세션</th><td>${escapeHtml(s.stamp || "")}${s.sim_only ? " · 시뮬레이션" : ""}${s.rebalance_done ? " · 리밸런싱 끝" : ""}</td></tr>
        <tr><th>예수금</th><td>${fmtNum(s.remain_money)}</td></tr>
        <tr><th>슬리브</th><td>jab ${(sleeves.jab * 100).toFixed(0)}% / mo ${(sleeves.mo * 100).toFixed(0)}% / d2 ${(sleeves.d2 * 100).toFixed(0)}%</td></tr>
        <tr><th>현금비중</th><td>${s.cash_weight == null ? "" : (Number(s.cash_weight) * 100).toFixed(1) + "%"}</td></tr>
        <tr><th>결합비중</th><td>${escapeHtml(comb || "없음")}</td></tr>
        <tr><th>저장 위치</th><td><code>${escapeHtml(r.saved_to || "")}</code></td></tr>
      </table>
      <div class="section-title">보유 (${(r.holdings || []).length})</div>
      <table class="kv">${holdRows || "<tr><td>없음</td></tr>"}</table>
      <div class="section-title">리밸런싱 (${(r.rebalance || []).length})</div>
      <table class="kv">${rebalRows || "<tr><td>없음</td></tr>"}</table>
      <div class="section-title">한줄 결론</div>
      <div>${escapeHtml(s.one_liner || "")}</div>`;
    $("analysisMeta").textContent = `${s.n_sessions || 0} session(s) · ${(r.series || []).length} chart points`;
    drawSeries(r.series || []);
  }

  function renderNarrative(payload, r) {
    const nar = payload.narrative || state.narrativeByProject[payload.project_id];
    const meta = $("narrativeMeta");
    if (!nar) {
      $("narrativeBody").textContent = "— (enable Auto-on-analyze or click Generate)";
      meta.textContent = "";
      return;
    }
    if (nar.ok) {
      $("narrativeBody").textContent = nar.narrative || "(empty response)";
      const u = nar.usage || {};
      const bits = [`gpt-5.6-sol · ${nar.duration_ms || "?"} ms`];
      if (u.input_tokens != null) bits.push(`in ${u.input_tokens} · out ${u.output_tokens ?? "?"} tok`);
      if (u.total_tokens != null) bits.push(`${u.total_tokens} total`);
      meta.textContent = bits.join(" · ");
    } else {
      $("narrativeBody").textContent = "Narrative failed";
      meta.textContent = nar.error || "unknown error";
    }
  }

  function renderResult(payload) {
    const r = payload.result;
    if (payload.project_id !== state.projectId) return;
    state.resultsByProject[payload.project_id] = r;
    if (payload.narrative !== undefined) {
      state.narrativeByProject[payload.project_id] = payload.narrative;
    }
    results.hidden = false;
    if (payload.project_id === "coin") renderCoin(r);
    else if (payload.project_id === "smallcap") renderSmallcap(r);
    else if (payload.project_id === "bollinger") renderBollinger(r);
    else if (payload.project_id === "strategyd") renderStrategyD(r);
    else if (payload.project_id === "assetalloc") renderAssetAlloc(r);
    else renderSuperma(r);
    renderNarrative(payload, r);
    dropZone.classList.add("compact");
  }

  // -------------------- Chart --------------------
  const chartState = { series: [], points: [], canvas: null, dpr: 1 };

  function drawSeries(series) {
    const panel = $("chartPanel");
    const canvas = $("chartCanvas");
    chartState.canvas = canvas;
    if (!series.length) { panel.hidden = true; return; }
    panel.hidden = false;
    chartState.series = series;
    const dpr = window.devicePixelRatio || 1;
    chartState.dpr = dpr;
    const cssW = canvas.clientWidth || 800;
    const cssH = 200;
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
    canvas.style.height = `${cssH}px`;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const rets = series.map((s) => s.ret_pct);
    const dds = series.map((s) => s.dd_pct);
    const minY = Math.min(...rets, ...dds, 0);
    const maxY = Math.max(...rets, ...dds, 0);
    const padL = 42, padR = 12, padT = 14, padB = 24;
    const plotW = cssW - padL - padR;
    const plotH = cssH - padT - padB;
    const yScale = (v) => padT + (maxY === minY ? plotH / 2 : (1 - (v - minY) / (maxY - minY)) * plotH);
    const xScale = (i) => padL + (i / Math.max(series.length - 1, 1)) * plotW;

    // Axes
    ctx.strokeStyle = "#2a3530"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(padL, padT); ctx.lineTo(padL, padT + plotH); ctx.lineTo(padL + plotW, padT + plotH); ctx.stroke();

    // Y ticks (5)
    ctx.fillStyle = "#8a9a91"; ctx.font = "10px monospace";
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (let i = 0; i <= 4; i++) {
      const v = minY + ((maxY - minY) * i) / 4;
      const y = yScale(v);
      ctx.strokeStyle = "#2a3530";
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke();
      ctx.fillText(v.toFixed(1), padL - 4, y);
    }

    // Zero line
    ctx.strokeStyle = "#3d5648"; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(padL, yScale(0)); ctx.lineTo(padL + plotW, yScale(0)); ctx.stroke();
    ctx.setLineDash([]);

    // X labels (few dates)
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const nLabels = Math.min(6, series.length);
    for (let i = 0; i < nLabels; i++) {
      const idx = Math.round((i / Math.max(nLabels - 1, 1)) * (series.length - 1));
      const s = series[idx];
      if (!s) continue;
      const x = xScale(idx);
      const short = (s.utc || "").slice(5, 10);
      ctx.fillText(short, x, padT + plotH + 4);
    }

    // Lines
    const line = (vals, color) => {
      ctx.strokeStyle = color; ctx.lineWidth = 1.6;
      ctx.beginPath();
      vals.forEach((v, i) => {
        const x = xScale(i), y = yScale(v);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
    };
    line(rets, "#3d9b6a");
    line(dds, "#c45c5c");

    chartState.points = series.map((s, i) => ({
      x: xScale(i), yRet: yScale(s.ret_pct), yDd: yScale(s.dd_pct),
      utc: s.utc, ret: s.ret_pct, dd: s.dd_pct,
    }));
  }

  const tip = $("chartTip");
  $("chartCanvas").addEventListener("mousemove", (e) => {
    if (!chartState.points.length) return;
    const canvas = chartState.canvas;
    const rect = canvas.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    let bestI = 0, bestD = Infinity;
    chartState.points.forEach((p, i) => {
      const d = Math.abs(p.x - cx);
      if (d < bestD) { bestD = d; bestI = i; }
    });
    const p = chartState.points[bestI];
    tip.hidden = false;
    tip.style.left = `${p.x}px`;
    tip.style.top = `${Math.min(p.yRet, p.yDd)}px`;
    tip.innerHTML = `<strong>${escapeHtml(p.utc)}</strong><br/>ret ${p.ret.toFixed(2)}% · dd ${p.dd.toFixed(2)}%`;
  });
  $("chartCanvas").addEventListener("mouseleave", () => { tip.hidden = true; });

  // -------------------- Analyze / Sheet --------------------
  async function analyze(pushSheet) {
    if (!state.projectId || !state.files.length) return;
    const requestProject = state.projectId;
    const token = ++state.requestToken;
    hideBanner();
    const projMeta = state.projects.find((x) => x.id === requestProject);
    const autoSheet = requestProject === "smallcap" && Boolean(projMeta && projMeta.sheet_push_available);
    const wantSheet = Boolean(pushSheet || autoSheet);
    const primaryBtn = pushSheet ? $("analyzePushBtn") : $("analyzeBtn");
    setBusy(primaryBtn, true, wantSheet ? "Analyzing…" : "Analyzing…");
    setBusy(pushSheet ? $("analyzeBtn") : $("analyzePushBtn"), true);
    showBanner("info", wantSheet ? "Analyzing log and updating Google Sheet…" : "Analyzing log…");

    const fd = new FormData();
    for (const file of state.files) {
      fd.append("files", await gzipFile(file));
    }
    fd.append("push_sheet", wantSheet ? "true" : "false");
    fd.append("use_openai", $("useOpenai").checked ? "true" : "false");

    try {
      const res = await apiFetch(`/api/analyze/${requestProject}`, { method: "POST", body: fd });
      const data = await readJson(res);
      if (!data.ok) {
        if (token === state.requestToken && requestProject === state.projectId) {
          showBanner("error", data.error || "Analyze failed", data.trace || null);
        }
        return;
      }
      state.resultsByProject[requestProject] = data.result;
      if (data.narrative !== undefined) state.narrativeByProject[requestProject] = data.narrative;
      if (token === state.requestToken && requestProject === state.projectId) {
        renderResult(data);
      }
      const sheet = data.result && data.result.sheet;
      if (sheet && sheet.ok) {
        if (token === state.requestToken && requestProject === state.projectId) {
          showBanner("ok", "Analysis complete and Google Sheet updated.");
        }
      } else if (wantSheet) {
        if (token === state.requestToken && requestProject === state.projectId) {
          showBanner("info", "Step 2/2 · Updating Google Sheet…");
        }
        setBusy(primaryBtn, true, "Updating Sheet…");
        const pushed = await pushCurrentSheet(requestProject, token, /*silent*/ true);
        if (token === state.requestToken && requestProject === state.projectId) {
          if (pushed) showBanner("ok", "Analysis complete and Google Sheet updated.");
        }
      } else if (token === state.requestToken && requestProject === state.projectId) {
        showBanner("ok", "Analysis complete.");
      }
    } catch (e) {
      if (token === state.requestToken) {
        showBanner("error", `Request failed: ${String(e)}`, "The local server may have restarted. Reload the page and try again.");
      }
    } finally {
      if (requestProject === state.projectId) {
        const p = currentProject();
        setBusy($("analyzeBtn"), false);
        setBusy($("analyzePushBtn"), false);
        $("analyzeBtn").disabled = state.files.length === 0;
        $("analyzePushBtn").disabled = state.files.length === 0 || !p.sheet_push_available;
      }
      refreshProjectState(requestProject);
    }
  }

  $("analyzeBtn").onclick = () => analyze(false);
  $("analyzePushBtn").onclick = () => analyze(true);

  async function pushCurrentSheet(projectId = state.projectId, token = state.requestToken, silent = false) {
    if (!projectId) return false;
    try {
      const res = await apiFetch(`/api/push/${encodeURIComponent(projectId)}`, { method: "POST" });
      const data = await readJson(res);
      if (!res.ok || !data.ok) throw new Error(data.detail || data.error || `Sheet push failed (${res.status})`);
      if (projectId === state.projectId && token === state.requestToken && !silent) {
        showBanner("ok", "Google Sheet updated from current local files.");
      }
      return true;
    } catch (e) {
      if (projectId === state.projectId && token === state.requestToken) {
        showBanner("error", `Google Sheet update failed: ${String(e)}`);
      }
      return false;
    } finally {
      refreshProjectState(projectId);
    }
  }

  $("repushBtn").onclick = async () => {
    if (!state.projectId) return;
    const p = currentProject();
    if (!p || !p.sheet_push_available) return;
    if (!confirm(`Rewrite the Google Sheet tab "${p.sheet_tab}" with the current local files?\n\nThis clears and re-writes the tab.`)) {
      return;
    }
    const token = ++state.requestToken;
    setBusy($("repushBtn"), true, "Updating…");
    hideBanner();
    showBanner("info", "Updating Google Sheet from current local files…");
    await pushCurrentSheet(state.projectId, token, false);
    setBusy($("repushBtn"), false);
  };

  // -------------------- Narrative --------------------
  $("narrateBtn").onclick = async () => {
    if (!state.openaiConfigured) {
      showBanner("warn", "OPENAI_API_KEY is not configured on the server.");
      return;
    }
    const r = state.resultsByProject[state.projectId];
    if (!r) { showBanner("warn", "Run analyze first before generating a narrative."); return; }
    setBusy($("narrateBtn"), true, "Asking gpt-5.6-sol…");
    hideBanner();
    try {
      const res = await apiFetch("/api/narrate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: state.projectId,
          analysis: r,
        }),
      });
      const data = await readJson(res);
      state.narrativeByProject[state.projectId] = data;
      renderNarrative({ project_id: state.projectId, narrative: data });
      if (data.ok) showBanner("ok", "Narrative generated with gpt-5.6-sol.");
      else showBanner("error", data.error || "Narrative failed");
    } catch (e) {
      showBanner("error", String(e));
    } finally {
      setBusy($("narrateBtn"), false);
      $("narrateBtn").disabled = !state.openaiConfigured;
    }
  };

  // -------------------- Log files panel --------------------
  async function loadLogFiles(projectId = state.projectId) {
    const project = state.projects.find((p) => p.id === projectId);
    $("logsTitle").textContent = `Log files — ${project ? project.short : projectId}`;
    $("logsBody").innerHTML = '<div class="muted">Loading log files…</div>';
    try {
      const res = await apiFetch(`/api/projects/${encodeURIComponent(projectId)}/logs`, { cache: "no-store" });
      if (!res.ok) throw new Error(`Log list failed (${res.status})`);
      const data = await res.json();
      if (projectId !== state.projectId || !state.logsOpen) return;
      const rows = data.items || [];
      if (!rows.length) {
        $("logsBody").innerHTML = '<div class="muted">No .log files found.</div>';
        return;
      }
      $("logsBody").innerHTML = `
        <table class="logs-table">
          <thead><tr><th>File</th><th>Location</th><th>Size</th><th>Modified</th><th>Full path</th></tr></thead>
          <tbody>${rows.map((item) => `
            <tr class="log-row ${item.source === "Project" ? "active" : ""}"
                data-log-id="${escapeHtml(item.id)}" data-log-name="${escapeHtml(item.name)}"
                title="Click to view this log">
              <td class="log-name">${escapeHtml(item.name)}</td>
              <td><span class="badge ${item.source.toLowerCase()}">${escapeHtml(item.source)}</span></td>
              <td>${formatBytes(item.size)}</td>
              <td>${escapeHtml(item.modified)}</td>
              <td class="log-path">${escapeHtml(item.path)}</td>
            </tr>`).join("")}
          </tbody>
        </table>
        <div class="table-cap"><span>${rows.length} file(s) · ● = live project file used by the analyzer</span></div>`;
      $("logsBody").querySelectorAll(".log-row").forEach((row) => {
        row.addEventListener("click", () => {
          showSelectedLog(row.dataset.logId, row.dataset.logName, projectId);
        });
      });
    } catch (e) {
      if (projectId === state.projectId) {
        $("logsBody").textContent = `Could not load log files: ${String(e)}`;
        showBanner("error", String(e));
      }
    }
  }

  $("logsBtn").onclick = async () => {
    if (!state.projectId) return;
    state.logsOpen = true;
    $("logsPanel").hidden = false;
    await loadLogFiles(state.projectId);
    $("logsPanel").scrollIntoView({ behavior: "smooth", block: "start" });
  };
  $("logsRefresh").onclick = () => loadLogFiles(state.projectId);
  $("logsClose").onclick = () => { state.logsOpen = false; $("logsPanel").hidden = true; };

  // -------------------- Content viewer --------------------
  const LOG_RULES = [
    { re: /(datetime\.datetime\([^)]+\))/g, cls: "log-ts" },
    { re: /(Traceback \(most recent call last\)|Exception|Error(?:s)?|실패)/g, cls: "log-err" },
    { re: /(실행 완료|OK|success|성공)/g, cls: "log-ok" },
    { re: /(FIXED 매수조건 만족|패스트매수조건 만족|슬로우 매수조건 만족|매도 조건)/g, cls: "log-signal" },
    { re: /(파킹ETF [^\n]+)/g, cls: "log-parking" },
    { re: /(OrderInfo\s*:\s*\{[^}]*\})/g, cls: "log-order" },
    { re: /(리밸런싱수량:\s*-?[1-9]\d*)/g, cls: "log-rebalance-nz" },
  ];

  function highlightLog(text) {
    let escaped = escapeHtml(text);
    for (const { re, cls } of LOG_RULES) {
      escaped = escaped.replace(re, `<span class="${cls}">$1</span>`);
    }
    return escaped;
  }

  async function loadContentList(projectId = state.projectId) {
    state.selectedLogId = null;
    state.selectedLogName = null;
    $("contentBackBtn").hidden = true;
    $("contentSelect").hidden = false;
    $("contentSearch").value = "";
    $("contentSearchMeta").textContent = "";
    const project = state.projects.find((p) => p.id === projectId);
    $("contentTitle").textContent = `Current content — ${project ? project.short : projectId}`;
    $("contentBody").textContent = "Loading current files…";
    try {
      const res = await apiFetch(`/api/projects/${encodeURIComponent(projectId)}/content`, { cache: "no-store" });
      if (!res.ok) throw new Error(`Content list failed (${res.status})`);
      const data = await res.json();
      if (projectId !== state.projectId || !state.contentOpen) return;
      const sel = $("contentSelect");
      sel.innerHTML = "";
      (data.items || []).forEach((it) => {
        const opt = document.createElement("option");
        opt.value = it.id;
        opt.textContent = `${it.label}${it.exists ? ` · ${formatBytes(it.size)}` : " (missing)"}`;
        sel.appendChild(opt);
      });
      if (data.items && data.items.length) await loadContentBody(data.items[0].id, projectId);
      else $("contentBody").textContent = "No content files configured for this project.";
    } catch (e) {
      if (projectId === state.projectId) {
        $("contentBody").textContent = `Could not load current content: ${String(e)}`;
        showBanner("error", String(e));
      }
    }
  }

  async function loadContentBody(id, projectId = state.projectId) {
    if (!id) return;
    $("contentBody").textContent = "Loading file…";
    try {
      const res = await apiFetch(
        `/api/projects/${encodeURIComponent(projectId)}/content/${encodeURIComponent(id)}`,
        { cache: "no-store" },
      );
      if (!res.ok) throw new Error(`File load failed (${res.status})`);
      const data = await res.json();
      if (projectId !== state.projectId || !state.contentOpen) return;
      renderFile(data);
    } catch (e) {
      if (projectId === state.projectId) {
        $("contentBody").textContent = `Could not load file: ${String(e)}`;
        showBanner("error", String(e));
      }
    }
  }

  function renderFile(data) {
    const header = `<span class="muted">${escapeHtml(data.label || data.name || "")}\n${escapeHtml(data.path)}\n${"─".repeat(60)}</span>\n`;
    $("contentBody").innerHTML = data.exists ? header + highlightLog(data.text) : `${header}(file missing)`;
    $("contentSearch").value = "";
    $("contentSearchMeta").textContent = "";
    state.lastSearch = { query: "", index: 0 };
  }

  async function showSelectedLog(logId, logName, projectId = state.projectId) {
    state.selectedLogId = logId;
    state.selectedLogName = logName;
    state.contentOpen = true;
    $("contentPanel").hidden = false;
    $("contentSelect").hidden = true;
    $("contentBackBtn").hidden = false;
    $("contentTitle").textContent = `Selected log — ${logName}`;
    $("contentBody").textContent = "Loading selected log…";
    try {
      const res = await apiFetch(
        `/api/projects/${encodeURIComponent(projectId)}/log-content/${encodeURIComponent(logId)}`,
        { cache: "no-store" },
      );
      if (!res.ok) throw new Error(`Log load failed (${res.status})`);
      const data = await res.json();
      if (projectId !== state.projectId || logId !== state.selectedLogId) return;
      renderFile(data);
      $("contentPanel").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      if (projectId === state.projectId) {
        $("contentBody").textContent = `Could not load selected log: ${String(e)}`;
        showBanner("error", String(e));
      }
    }
  }

  $("viewBtn").onclick = async () => {
    if (!state.projectId) return;
    state.contentOpen = true;
    $("contentPanel").hidden = false;
    await loadContentList(state.projectId);
    $("contentPanel").scrollIntoView({ behavior: "smooth", block: "start" });
  };
  $("contentBackBtn").onclick = () => loadContentList(state.projectId);
  $("contentRefresh").onclick = () => {
    if (state.selectedLogId) showSelectedLog(state.selectedLogId, state.selectedLogName, state.projectId);
    else loadContentBody($("contentSelect").value, state.projectId);
  };
  $("contentSelect").onchange = () => loadContentBody($("contentSelect").value, state.projectId);

  // -------------------- In-panel search --------------------
  function runSearch(next = true) {
    const q = $("contentSearch").value.trim();
    const body = $("contentBody");
    // Reset previous marks
    body.querySelectorAll("mark").forEach((m) => {
      const parent = m.parentNode;
      parent.replaceChild(document.createTextNode(m.textContent), m);
      parent.normalize();
    });
    if (!q) { $("contentSearchMeta").textContent = ""; state.lastSearch = { query: "", index: 0 }; return; }

    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, null);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    const lc = q.toLowerCase();
    const matches = [];
    nodes.forEach((node) => {
      const text = node.textContent;
      let idx = 0;
      while ((idx = text.toLowerCase().indexOf(lc, idx)) !== -1) {
        matches.push({ node, start: idx, end: idx + q.length });
        idx += q.length;
      }
    });
    if (!matches.length) {
      $("contentSearchMeta").textContent = "0 matches";
      state.lastSearch = { query: q, index: 0 };
      return;
    }
    // Wrap all matches (in reverse per node so offsets stay valid)
    const byNode = new Map();
    matches.forEach((m) => {
      const arr = byNode.get(m.node) || [];
      arr.push(m); byNode.set(m.node, arr);
    });
    const allMarks = [];
    for (const [node, list] of byNode) {
      list.sort((a, b) => b.start - a.start);
      list.forEach((m) => {
        const text = node.textContent;
        const before = text.slice(0, m.start);
        const middle = text.slice(m.start, m.end);
        const after = text.slice(m.end);
        const mark = document.createElement("mark");
        mark.textContent = middle;
        const parent = node.parentNode;
        parent.insertBefore(document.createTextNode(after), node.nextSibling);
        parent.insertBefore(mark, node.nextSibling);
        node.textContent = before;
        allMarks.unshift(mark); // preserve document order (this node reversed)
      });
    }
    // Collect all marks in document order
    const orderedMarks = Array.from(body.querySelectorAll("mark"));
    const activeIndex = q === state.lastSearch.query
      ? (state.lastSearch.index + (next ? 1 : -1) + orderedMarks.length) % orderedMarks.length
      : 0;
    orderedMarks.forEach((m, i) => m.classList.toggle("active", i === activeIndex));
    orderedMarks[activeIndex].scrollIntoView({ block: "center", behavior: "smooth" });
    state.lastSearch = { query: q, index: activeIndex };
    $("contentSearchMeta").textContent = `${activeIndex + 1} of ${orderedMarks.length}`;
  }
  $("contentSearch").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); runSearch(!e.shiftKey); }
    if (e.key === "Escape") { $("contentSearch").value = ""; runSearch(); }
  });

  // -------------------- Keyboard shortcuts --------------------
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    if (e.key >= "1" && e.key <= "9") {
      const idx = parseInt(e.key, 10) - 1;
      if (state.projects[idx]) selectProject(state.projects[idx].id);
    }
  });

  // -------------------- Boot --------------------
  loadRuntimeConfig();
  fetch("/api/projects", { cache: "no-store" })
    .then((r) => r.json())
    .then((data) => {
      state.projects = data.projects || [];
      renderNav();
      if (state.projects.length) selectProject(state.projects[0].id);
      // Refresh sidebar times every 30s so 'just now' → '1m ago' updates
      setInterval(() => { renderNav(); renderTopMeta(); }, 30000);
    })
    .catch((e) => showBanner("error", String(e)));
})();
