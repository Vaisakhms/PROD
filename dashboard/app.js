"use strict";

/* Market Pulse dashboard app.js — replacement, compatible with Lightweight Charts 4.2.2 */
const DATA_URL = "./data/latest.json";
const HISTORY_INDEX_URL = "./data/history/index.json";
const SNAPSHOT_BASE_URL = "./data/snapshots/";

const state = {
    data: null,
    history: [],
    selectedIndex: "Nifty 50",
    selectedTimeframe: "Daily",
    charts: [],
    loading: false,
    syncingCharts: false
};

const $ = (id) => document.getElementById(id);
const byId = $;

function number(value, fallback = null) {
    if (value === null || value === undefined || value === "") return fallback;
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
}

function formatNumber(value, decimals = 2) {
    const n = number(value);
    return n === null ? "--" : n.toLocaleString("en-IN", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
    });
}

function formatCompact(value) {
    const n = number(value);
    return n === null ? "--" : new Intl.NumberFormat("en-IN", {
        notation: "compact",
        maximumFractionDigits: 2
    }).format(n);
}

function formatPercent(value) {
    const n = number(value);
    return n === null ? "--" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function formatSigned(value, decimals = 2) {
    const n = number(value);
    return n === null ? "--" : `${n > 0 ? "+" : ""}${n.toFixed(decimals)}`;
}

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function setText(id, value) {
    const el = $(id);
    if (el) el.textContent = value ?? "--";
}

function setHtml(id, html) {
    const el = $(id);
    if (el) el.innerHTML = html;
}

function setWidth(id, value) {
    const el = $(id);
    if (el) {
        el.style.width =
            `${Math.max(0, Math.min(100, number(value, 0)))}%`;
    }
}

function sentimentClass(value) {
    const text = String(value ?? "").toLowerCase();

    if (
        text.includes("bullish") ||
        text.includes("positive") ||
        text.includes("long buildup")
    ) {
        return "positive";
    }

    if (
        text.includes("bearish") ||
        text.includes("negative") ||
        text.includes("short buildup")
    ) {
        return "negative";
    }

    return "neutral";
}

function optionOiSentiment(buildup, optionType) {
    const value = String(buildup ?? "").toLowerCase();
    const type = String(optionType ?? "").toLowerCase();

    if (
        !value ||
        value.includes("no change") ||
        value.includes("unavailable") ||
        value.includes("unknown")
    ) {
        return { label: "Neutral", className: "neutral" };
    }

    if (type === "call") {
        if (
            value.includes("long buildup") ||
            value.includes("short covering")
        ) {
            return { label: "Bullish", className: "positive" };
        }

        if (
            value.includes("short buildup") ||
            value.includes("long unwinding")
        ) {
            return { label: "Bearish", className: "negative" };
        }
    }

    if (type === "put") {
        if (
            value.includes("short buildup") ||
            value.includes("long unwinding")
        ) {
            return { label: "Bullish", className: "positive" };
        }

        if (
            value.includes("long buildup") ||
            value.includes("short covering")
        ) {
            return { label: "Bearish", className: "negative" };
        }
    }

    return { label: "Neutral", className: "neutral" };
}

function showToast(message) {
    const toast = $("toast");
    if (!toast) return;

    toast.textContent = message;
    toast.classList.add("show");

    clearTimeout(showToast.timer);

    showToast.timer = setTimeout(() => {
        toast.classList.remove("show");
    }, 3000);
}

function formatDashboardDateTime(value) {
    if (!value) return "--";

    const raw = String(value).replace(" ", "T");

    const date = new Date(
        /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(raw)
            ? `${raw}+05:30`
            : raw
    );

    if (Number.isNaN(date.getTime())) return String(value);

    const day = Number(
        new Intl.DateTimeFormat("en-IN", {
            day: "numeric",
            timeZone: "Asia/Kolkata"
        }).format(date)
    );

    const suffix =
        day >= 11 && day <= 13
            ? "th"
            : ({ 1: "st", 2: "nd", 3: "rd" }[day % 10] || "th");

    const datePart = new Intl.DateTimeFormat("en-GB", {
        day: "numeric",
        month: "long",
        year: "numeric",
        timeZone: "Asia/Kolkata"
    }).format(date);

    const timePart = new Intl.DateTimeFormat("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
        timeZone: "Asia/Kolkata"
    }).format(date).toUpperCase();

    return `${datePart.replace(/^\d+/, `${day}${suffix}`)} : ${timePart}`;
}

async function fetchJson(url) {
    const separator = url.includes("?") ? "&" : "?";

    const response = await fetch(
        `${url}${separator}_=${Date.now()}`,
        { cache: "no-store" }
    );

    if (!response.ok) {
        throw new Error(`${url} returned HTTP ${response.status}`);
    }

    return response.json();
}

function safeHttpUrl(value) {
    if (!value) return "";

    try {
        const url = new URL(value, window.location.href);

        return url.protocol === "https:" || url.protocol === "http:"
            ? url.href
            : "";
    } catch (_) {
        return "";
    }
}

// ---------- Overall market score ----------

function renderOverall(data) {
    const overall = data.overall || {};
    const score = number(overall.score, 0);
    const coverage = number(overall.coverage_pct, 0);

    // Convert the model's -100..100 score to the gauge's 0..100 scale.
    const gaugeScore = Math.max(
        0,
        Math.min(100, (score + 100) / 2)
    );

    setText("overallScore", score.toFixed(1));
    setText("overallLabel", overall.label || "Market bias unavailable");
    setText("scoreGaugeValue", gaugeScore.toFixed(0));
    setText("coverageValue", `${coverage.toFixed(0)}%`);

    setText(
        "dataTimestamp",
        formatDashboardDateTime(data.generated_at_ist || data.generated_at)
    );

    setText("sessionSummary", data.market_status || "--");
    setText("marketStatus", data.market_status || "Market status unavailable");

    setText(
        "lastUpdated",
        formatDashboardDateTime(data.generated_at_ist || data.generated_at)
    );

    setWidth("scoreTrackFill", gaugeScore);
    setWidth("coverageFill", coverage);

    const gauge = $("scoreGauge");

    if (gauge) {
        gauge.style.background =
            `conic-gradient(var(--accent, #38bdf8) ${gaugeScore * 3.6}deg, var(--border, #263449) 0deg)`;
    }

    setText(
        "scoreNote",
        `Overall model score: ${score.toFixed(2)}. Coverage: ${coverage.toFixed(0)}%.`
    );

    setText("dataStatus", "Data loaded");
}

// ---------- Index cards and technical summaries ----------

function renderIndexCards(data) {
    const container = $("indexCards");
    if (!container) return;

    const indices = data.indices || {};
    const entries = Object.entries(indices);

    if (!entries.length) {
        container.innerHTML =
            '<div class="loading-card">No index data available.</div>';
        return;
    }

    container.innerHTML = entries.map(([name, item]) => {
        const daily = item.timeframes?.Daily || {};
        const intraday = item.timeframes?.["15m"] || daily;
        const ltp = number(item.ltp, number(daily.close));
        const change = number(daily.chg, 0);

        return `
            <article class="panel index-card">
                <div class="panel-heading">
                    <div>
                        <span class="eyebrow">${escapeHtml(name)}</span>
                        <h3>${formatNumber(ltp)}</h3>
                    </div>
                    <span class="change-pill ${change >= 0 ? "positive" : "negative"}">
                        ${formatPercent(change)}
                    </span>
                </div>
                <div class="index-card-footer">
                    <span class="status-pill ${sentimentClass(daily.label)}">
                        ${escapeHtml(daily.label || "Unavailable")}
                    </span>
                    <span class="muted small-text">
                        15m: ${escapeHtml(intraday.label || "--")}
                    </span>
                </div>
                <div class="summary-row">
                    <span>Daily RSI</span>
                    <strong>${formatNumber(daily.rsi, 1)}</strong>
                </div>
                <div class="summary-row">
                    <span>Daily score</span>
                    <strong>${formatSigned(daily.score)}</strong>
                </div>
                <button class="text-button select-index-button"
                    type="button" data-index="${escapeHtml(name)}">
                    View chart
                </button>
            </article>
        `;
    }).join("");

    container.querySelectorAll("[data-index]").forEach(button => {
        button.addEventListener("click", () => {
            state.selectedIndex = button.dataset.index;

            const select = $("indexSelect");
            if (select) select.value = state.selectedIndex;

            renderChart();

            $("priceChart")?.scrollIntoView({
                behavior: "smooth",
                block: "center"
            });
        });
    });
}

function renderTechnicalCards(data) {
    const container = $("technicalCards");
    if (!container) return;

    const entries = Object.entries(data.indices || {});

    if (!entries.length) {
        container.innerHTML =
            '<div class="loading-card">No technical data available.</div>';
        return;
    }

    container.innerHTML = entries.map(([name, item]) => {
        const frames = item.timeframes || {};

        const rows = ["15m", "30m", "1h", "Daily"].map(frame => {
            const t = frames[frame] || {};
            const sma = t.sma || {};

            return `
                <div class="technical-row">
                    <div>
                        <strong>${escapeHtml(frame)}</strong>
                        <span class="muted small-text">
                            ${escapeHtml(t.label || "--")}
                        </span>
                    </div>
                    <div class="technical-values">
                        <span>RSI ${formatNumber(t.rsi, 1)}</span>
                        <span class="status-pill ${sentimentClass(t.label)}">
                            ${formatSigned(t.score)}
                        </span>
                    </div>
                    <div class="technical-sma muted small-text">
                        Close ${formatNumber(t.close)} ·
                        SMA20 ${formatNumber(sma["20"])} ·
                        SMA50 ${formatNumber(sma["50"])} ·
                        SMA100 ${formatNumber(sma["100"])}
                    </div>
                </div>
            `;
        }).join("");

        return `
            <article class="panel technical-card">
                <h3>${escapeHtml(name)}</h3>
                ${rows}
            </article>
        `;
    }).join("");
}

function renderWeeklyCards(data) {
    const container = $("weeklyCards");
    if (!container) return;

    const entries = Object.entries(data.indices || {});

    if (!entries.length) {
        container.innerHTML =
            '<div class="loading-card">No recent performance data.</div>';
        return;
    }

    container.innerHTML = entries.map(([name, item]) => {
        const week = item.seven_day || {};
        const change = number(week.net, 0);
        const days = Array.isArray(week.chg) ? week.chg : [];

        const bars = days.map((v, i) => {
            const n = number(v, 0);
            const height = Math.max(4, Math.min(100, Math.abs(n) * 25));

            return `
                <div class="weekly-bar-wrap"
                    title="Session ${i + 1}: ${formatPercent(n)}">
                    <div class="weekly-bar ${n >= 0 ? "positive" : "negative"}"
                        style="height:${height}%"></div>
                    <span>${n >= 0 ? "+" : ""}${n.toFixed(1)}</span>
                </div>
            `;
        }).join("");

        return `
            <article class="panel weekly-card">
                <div class="panel-heading">
                    <div>
                        <span class="eyebrow">${escapeHtml(name)}</span>
                        <h3>${formatPercent(change)}</h3>
                    </div>
                    <span class="status-pill ${change >= 0 ? "positive" : "negative"}">
                        ${number(week.up, 0)} positive sessions
                    </span>
                </div>
                <div class="weekly-bars">${bars}</div>
                <div class="summary-row">
                    <span>7-session high</span>
                    <strong>${formatNumber(week.hi)}</strong>
                </div>
                <div class="summary-row">
                    <span>7-session low</span>
                    <strong>${formatNumber(week.lo)}</strong>
                </div>
                <div class="summary-row">
                    <span>Range position</span>
                    <strong>${formatNumber(week.pos, 1)}%</strong>
                </div>
            </article>
        `;
    }).join("");
}
// ---------- Options ----------

function renderOptions(data) {
    const options = data.options || {};
    const pcr = number(options.pcr);
    const zonePcr = number(options.zone_pcr);
    const capturedAt = options.captured_at
        ? formatDashboardDateTime(options.captured_at)
        : "unavailable";
    const closedNotice = /closed/i.test(data.market_status || "")
        ? "; values may reflect the last session"
        : "";

    setText("pcrValue", formatNumber(pcr, 2));

    let pcrDescription = "PCR unavailable";

    if (pcr !== null) {
        pcrDescription = pcr > 1.2
            ? "Put open interest is relatively higher"
            : pcr < 0.8
                ? "Call open interest is relatively higher"
                : "PCR is in a relatively balanced range";
    }

    setText("pcrLabel", pcrDescription);
    setText("nearAtmPcr", formatNumber(zonePcr, 2));
    setText("atmStrike", formatNumber(options.atm, 0));
    setText("resistanceValue", formatNumber(options.resistance, 0));
    setText("supportValue", formatNumber(options.support, 0));

    setText(
        "optionsSource",
        `Source: ${options.source || "Unavailable"} | Expiry: ${options.expiry || "--"} | Snapshot received: ${capturedAt} IST${closedNotice}`
    );

    const container = $("optionsCards");
    if (!container) return;

    const contracts = [
        { name: "ATM Call (CE)", data: options.ce || {}, type: "call" },
        { name: "ATM Put (PE)", data: options.pe || {}, type: "put" }
    ];

    container.innerHTML = contracts.map(contract => {
        const item = contract.data;
        const change = number(item.ltpch, 0);
        const sentiment = optionOiSentiment(item.buildup, contract.type);

        return `
            <article class="panel option-card">
                <div class="panel-heading">
                    <div>
                        <span class="eyebrow">${escapeHtml(contract.name)}</span>
                        <h3>${formatNumber(item.ltp)}</h3>
                    </div>
                    <span class="change-pill ${change >= 0 ? "positive" : "negative"}">
                        ${formatSigned(change)}
                    </span>
                </div>
                <div class="summary-row">
                    <span>Open interest</span>
                    <strong>${formatCompact(item.oi)}</strong>
                </div>
                <div class="summary-row">
                    <span>Change in OI</span>
                    <strong>${formatCompact(item.oich)}</strong>
                </div>
                <div class="summary-row">
                    <span>Positioning</span>
                    <strong class="status-pill ${sentimentClass(item.buildup)}">
                        ${escapeHtml(item.buildup || "--")}
                    </strong>
                </div>
                <div class="summary-row">
                    <span>OI Sentiment</span>
                    <strong class="status-pill ${sentiment.className}">
                        ${sentiment.label}
                    </strong>
                </div>
            </article>
        `;
    }).join("");
}

// ---------- Global markets, score breakdown and news ----------

function renderGlobalMarkets(data) {
    const container = $("globalCards");
    if (!container) return;

    const markets = Array.isArray(data.global_markets)
        ? data.global_markets
        : [];

    if (!markets.length) {
        container.innerHTML =
            '<div class="loading-card">No global market data available.</div>';
        return;
    }

    container.innerHTML = markets.map(market => {
        const change = number(market.change_pct, 0);
        const category = market.category === "risk"
            ? "Risk indicator"
            : "Equity index";

        return `
            <article class="panel global-card">
                <div class="panel-heading">
                    <div>
                        <span class="eyebrow">${escapeHtml(category)}</span>
                        <h3>${escapeHtml(market.name || market.ticker || "Market")}</h3>
                    </div>
                    <span class="change-pill ${change >= 0 ? "positive" : "negative"}">
                        ${formatPercent(change)}
                    </span>
                </div>
                <div class="global-price">${formatNumber(market.price)}</div>
                <div class="muted small-text">
                    ${escapeHtml(market.ticker || "")}
                </div>
            </article>
        `;
    }).join("");
}

function renderBreakdown(data) {
    const container = $("scoreBreakdown");
    if (!container) return;

    const components = Array.isArray(data.scores?.components)
        ? data.scores.components
        : [];

    if (!components.length) {
        container.innerHTML =
            '<div class="loading-card">No score components available.</div>';
        return;
    }

    container.innerHTML = components.map(item => {
        const contribution = number(item.contribution, 0);
        const score = number(item.score, 0);
        const weight = number(item.weight, 0);

        return `
            <div class="breakdown-row">
                <div class="breakdown-factor">
                    <strong>${escapeHtml(item.name || "Analysis factor")}</strong>
                    <span class="muted small-text">
                        Weight ${weight.toFixed(0)}% · Score ${formatSigned(score)}
                    </span>
                </div>
                <div class="breakdown-contribution ${contribution >= 0 ? "positive" : "negative"}">
                    ${formatSigned(contribution)}
                </div>
            </div>
        `;
    }).join("");
}

function renderNews(data) {
    const news = data.news || {};
    const items = Array.isArray(news.items) ? news.items : [];

    setText(
        "newsSummary",
        `News sentiment score: ${formatSigned(news.score)} | ${items.length} news items`
    );

    const container = $("newsCards");
    if (!container) return;

    if (!items.length) {
        container.innerHTML =
            '<div class="loading-card">No news items available.</div>';
        return;
    }

    container.innerHTML = items.slice(0, 12).map(item => {
        const age = number(item.age);

        const ageText = age === null
            ? ""
            : age < 24
                ? `${age.toFixed(1)} hours ago`
                : `${(age / 24).toFixed(1)} days ago`;

        const tags = (Array.isArray(item.tags) ? item.tags : [])
            .map(tag => `<span class="news-tag">${escapeHtml(tag)}</span>`)
            .join("");

        const url = safeHttpUrl(item.url);

        return `
            <article class="panel news-card">
                <div class="news-card-meta">
                    <span>${escapeHtml(item.source || "News source")}</span>
                    <span>${escapeHtml(ageText)}</span>
                </div>
                <h3>${escapeHtml(item.title || "Untitled news item")}</h3>
                <div class="news-tags">${tags}</div>
                ${url
                    ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Read story ↗</a>`
                    : ""}
            </article>
        `;
    }).join("");
}

function combinedMarketBias(data) {
    const components = Array.isArray(data.scores?.components)
        ? data.scores.components
        : [];
    const totalWeight = components.reduce(
        (total, item) => total + Math.max(0, number(item.weight, 0)),
        0
    );
    const weightedContribution = components.reduce((total, item) => {
        const contribution = number(item.contribution);
        const score = number(item.score, 0);
        const weight = number(item.weight, 0);
        return total + (contribution ?? score * weight);
    }, 0);
    const score = totalWeight > 0
        ? weightedContribution / totalWeight * 100
        : number(data.overall?.score);

    return {
        score,
        direction: score === null
            ? "Unavailable"
            : score >= 15
                ? "Bullish"
                : score <= -15
                    ? "Bearish"
                    : "Neutral / mixed",
        components
    };
}

function renderCombinedDecision(data) {
    const container = $("combinedDecisionCard");
    if (!container) return;

    const strategy = data.strategy || {};
    const options = data.options || {};
    const news = data.news || {};
    const indices = data.indices || {};
    const snapshotDate = data.generated_at || data.generated_at_ist || "";
    const snapshotTimestamp = Date.parse(snapshotDate);
    const snapshotAge = Number.isFinite(snapshotTimestamp)
        ? Date.now() - snapshotTimestamp
        : null;
    const staleSnapshot = snapshotAge === null
        || snapshotAge < 0
        || snapshotAge > 10 * 60 * 1000;
    const marketClosed = /closed/i.test(data.market_status || "")
        || staleSnapshot;
    const snapshotAgeLabel = snapshotAge === null
        ? "age unavailable"
        : snapshotAge < 0
            ? "timestamp is in the future"
            : snapshotAge < 60 * 1000
                ? "just now"
                : snapshotAge < 60 * 60 * 1000
                    ? `${Math.floor(snapshotAge / (60 * 1000))} min ago`
                    : `${Math.floor(snapshotAge / (60 * 60 * 1000))} hr ${Math.floor(snapshotAge % (60 * 60 * 1000) / (60 * 1000))} min ago`;
    const snapshotTimeLabel = Number.isFinite(snapshotTimestamp)
        ? `${formatDashboardDateTime(snapshotDate)} IST · ${snapshotAgeLabel}`
        : "Timestamp unavailable";
    const bias = combinedMarketBias(data);
    const mainDecision = ["BUY ATM CE", "BUY ATM PE"].includes(strategy.decision)
        ? strategy.decision
        : "WAIT";
    const scalp = strategy.scalp_opportunity || {};
    const scalpDecision = marketClosed
        ? "WAIT"
        : ["BUY ATM CE", "BUY ATM PE"].includes(scalp.decision)
            ? scalp.decision
            : scalp.decision === "WAIT"
                ? "WAIT"
                : "Unavailable";
    const candidate = mainDecision !== "WAIT"
        ? mainDecision
        : scalpDecision.startsWith("BUY ATM")
            ? scalpDecision
            : "WAIT";
    const candidateDirection = candidate.endsWith("CE")
        ? "Bullish"
        : candidate.endsWith("PE")
            ? "Bearish"
            : null;
    const conflictsWithBias = Boolean(
        candidateDirection
        && (bias.direction === "Bullish" || bias.direction === "Bearish")
        && candidateDirection !== bias.direction
    );
    const combinedDecision = marketClosed || conflictsWithBias
        ? "WAIT"
        : candidate;
    const isScalpOnly = mainDecision === "WAIT"
        && scalpDecision.startsWith("BUY ATM")
        && combinedDecision !== "WAIT";
    const decisionClass = combinedDecision === "BUY ATM CE"
        || combinedDecision === "BUY ATM PE"
        ? combinedDecision.endsWith("CE") ? "buy-call" : "buy-put"
        : "wait";
    const decisionText = combinedDecision === "WAIT"
        ? "WAIT"
        : `${isScalpOnly ? "SCALP ONLY · " : ""}${combinedDecision}`;
    const decisionReason = marketClosed
        ? /closed/i.test(data.market_status || "")
            ? "Market is closed; stored signals are not live trade calls."
            : `Market data is ${snapshotAgeLabel}; live signals are disabled when data is over 10 minutes old.`
        : conflictsWithBias
            ? `${candidate} conflicts with the weighted overall market bias. Wait for alignment or reassessment.`
            : candidate === "WAIT"
                ? "Neither the main strategy nor the scalp setup has a valid entry signal."
                : bias.direction === "Neutral / mixed"
                    ? "Signal passed; broad market inputs are mixed, so treat it cautiously."
                    : `Signal passed and agrees with the weighted ${bias.direction.toLowerCase()} market bias.`;

    const nifty = indices["Nifty 50"] || {};
    const niftyFrames = nifty.timeframes || {};
    const trend5m = strategy.trends?.["Nifty 50"]?.["5m"]
        || niftyFrames["5m"]?.label
        || "Unavailable";
    const trend15m = strategy.trends?.["Nifty 50"]?.["15m"]
        || niftyFrames["15m"]?.label
        || "Unavailable";
    const breadth = Object.entries(indices).map(([name, item]) => {
        const label = item.timeframes?.["15m"]?.label || "Unavailable";
        return { name, label, direction: sentimentClass(label) };
    });
    const bullishBreadth = breadth.filter(item => item.direction === "positive").length;
    const bearishBreadth = breadth.filter(item => item.direction === "negative").length;

    const oi = strategy.oi || {};
    const oiScore = number(options.oi_score);
    const oiSentiment = oi.verdict
        || (oiScore === null
            ? "Unavailable"
            : oiScore >= 0.15
                ? "Bullish"
                : oiScore <= -0.15
                    ? "Bearish"
                    : "Neutral");
    const pcr = number(oi.pcr) ?? number(options.pcr);
    const weekly = number(nifty.seven_day?.net);
    const globalScore = number(data.scores?.global_markets);
    const newsScore = number(news.score);
    const supportingComponents = bias.components
        .filter(item => number(item.score, 0) * (bias.score || 0) > 0)
        .sort((left, right) =>
            Math.abs(number(right.contribution, 0))
            - Math.abs(number(left.contribution, 0))
        )
        .slice(0, 2)
        .map(item => item.name)
        .filter(Boolean);
    const coverage = number(data.overall?.coverage_pct);
    const aggregateScore = bias.score === null
        ? "Unavailable"
        : `${bias.score > 0 ? "+" : ""}${bias.score.toFixed(1)} / 100`;

    container.innerHTML = `
        <div class="combined-decision-header">
            <div>
                <span class="eyebrow">CURRENT COMBINED DECISION</span>
                <div class="${decisionClass} combined-decision-value">
                    ${escapeHtml(decisionText)}
                </div>
                <p class="muted combined-decision-reason">${escapeHtml(decisionReason)}</p>
                <p class="combined-data-age${staleSnapshot ? " stale" : ""}">Last data: ${escapeHtml(snapshotTimeLabel)}</p>
            </div>
            <div class="combined-bias ${sentimentClass(bias.direction)}">
                <span class="eyebrow">WEIGHTED MARKET BIAS</span>
                <strong>${escapeHtml(bias.direction)}</strong>
                <span>${escapeHtml(aggregateScore)}</span>
            </div>
        </div>
        <div class="combined-metrics">
            <div class="combined-metric">
                <span class="eyebrow">MAIN STRATEGY</span>
                <strong>${escapeHtml(marketClosed ? "WAIT" : mainDecision)}</strong>
                <small>${escapeHtml(strategy.reasons?.[0] || "See detailed strategy filters.")}</small>
            </div>
            <div class="combined-metric">
                <span class="eyebrow">SCALP OPPORTUNITY</span>
                <strong class="${scalpDecision.endsWith("CE") ? "positive" : scalpDecision.endsWith("PE") ? "negative" : "neutral"}">${escapeHtml(scalpDecision)}</strong>
                <small>${escapeHtml(/closed/i.test(data.market_status || "") ? "Market closed." : staleSnapshot ? "Latest snapshot is stale; no live signal." : scalp.reason || "Scalp data unavailable.")}</small>
            </div>
            <div class="combined-metric">
                <span class="eyebrow">NIFTY TREND / BREADTH</span>
                <strong>${escapeHtml(trend5m)} · ${escapeHtml(trend15m)}</strong>
                <small>${bullishBreadth} bullish / ${bearishBreadth} bearish 15m indices</small>
            </div>
            <div class="combined-metric">
                <span class="eyebrow">OPTIONS / OI</span>
                <strong>${escapeHtml(oiSentiment)} · PCR ${pcr === null ? "--" : pcr.toFixed(2)}</strong>
                <small>ATM ${formatNumber(options.atm, 0)} · expiry ${escapeHtml(options.expiry || "--")}</small>
            </div>
            <div class="combined-metric">
                <span class="eyebrow">SEVEN-SESSION TREND</span>
                <strong>${formatPercent(weekly)}</strong>
                <small>Nifty 50 recent momentum</small>
            </div>
            <div class="combined-metric">
                <span class="eyebrow">GLOBAL / NEWS</span>
                <strong>${globalScore === null ? "--" : formatSigned(globalScore, 2)} global · ${newsScore === null ? "--" : formatSigned(newsScore, 2)} news</strong>
                <small>${Array.isArray(news.items) ? news.items.length : 0} news items in sentiment input</small>
            </div>
        </div>
        <div class="combined-footer muted small-text">
            Composite bias uses the weighted backend score components${coverage === null ? "" : ` · ${coverage.toFixed(0)}% input coverage`}${supportingComponents.length ? ` · largest ${bias.direction === "Bearish" ? "bearish" : "bullish"} inputs: ${escapeHtml(supportingComponents.join(", "))}` : ""}.
            ${conflictsWithBias ? " The signal is held at WAIT because the trade direction conflicts with strong aggregate bias." : ""}
            ${staleSnapshot && !/closed/i.test(data.market_status || "") ? ` Snapshot is stale (${escapeHtml(snapshotAgeLabel)}).` : ""}
            Signals are informational only; no broker orders are placed.
        </div>
    `;
}

// ---------- Price Action decision ----------

function renderPriceActionDecision(data) {
    const criteria = $("criteriaSummary");
    const decision = $("niftyTradeDecision");

    if (!criteria || !decision) return;

    const indices = data.indices || {};
    const options = data.options || {};

    const required = [
        "Nifty 50",
        "Bank Nifty",
        "Nifty Midcap",
        "Nifty 100"
    ];

    const rows = required.map(name => {
        const item = indices[name] || {};
        const frames = item.timeframes || {};
        const intraday = frames["15m"] || {};
        const daily = frames.Daily || {};

        const close = number(intraday.close, number(daily.close));
        const sma = intraday.sma || daily.sma || {};
        const sma20 = number(sma["20"]);
        const sma50 = number(sma["50"]);
        const sma100 = number(sma["100"]);

        let trend = intraday.label || daily.label || "Unavailable";

        if (close !== null && sma20 !== null && sma50 !== null) {
            if (close > sma20 && sma20 > sma50) {
                trend = "Bullish";
            } else if (close < sma20 && sma20 < sma50) {
                trend = "Bearish";
            } else {
                trend = "Mixed";
            }
        }

        return {
            name,
            close,
            trend,
            sma20,
            sma50,
            sma100,
            available: close !== null
        };
    });

    criteria.innerHTML = `
        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th>Index</th>
                        <th>Price</th>
                        <th>15m Trend</th>
                        <th>SMA 20</th>
                        <th>SMA 50</th>
                        <th>SMA 100</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows.map(row => `
                        <tr>
                            <td>${escapeHtml(row.name)}</td>
                            <td>${formatNumber(row.close)}</td>
                            <td>
                                <span class="status-pill ${sentimentClass(row.trend)}">
                                    ${escapeHtml(row.trend)}
                                </span>
                            </td>
                            <td>${formatNumber(row.sma20)}</td>
                            <td>${formatNumber(row.sma50)}</td>
                            <td>${formatNumber(row.sma100)}</td>
                        </tr>
                    `).join("")}
                </tbody>
            </table>
        </div>
    `;

    const strategy = data.strategy || {};
    const marketClosed = /closed/i.test(data.market_status || "");
    const scalpOpportunity = strategy.scalp_opportunity || {};
    const scalpDecision = marketClosed
        ? "WAIT"
        : scalpOpportunity.decision || "WAIT";
    const scalpReason = marketClosed
        ? "Market closed; stored scalp data is not a live signal."
        : scalpOpportunity.reason || "Scalp signal unavailable.";
    const scalpClass = scalpDecision === "BUY ATM CE"
        ? "buy-call"
        : scalpDecision === "BUY ATM PE"
            ? "buy-put"
            : "wait";
    const oi = strategy.oi || {};
    const latestPcr = number(oi.pcr) ?? number(data.options?.pcr);
    const pcrDisplay = latestPcr === null
        ? "Unavailable"
        : `${latestPcr.toFixed(2)}${number(oi.pcr) === null ? " (snapshot)" : ""}`;
    const signal = ["BUY ATM CE", "BUY ATM PE"].includes(strategy.decision)
        ? strategy.decision
        : "WAIT";
    const signalClass = signal === "BUY ATM CE"
        ? "buy-call"
        : signal === "BUY ATM PE"
            ? "buy-put"
            : "wait";
    const setups = Array.isArray(strategy.matched_setups)
        ? strategy.matched_setups
        : [];
    const reasons = Array.isArray(strategy.reasons) ? strategy.reasons : [];
    const position = strategy.position || {};
    const level = strategy.nearest_major_level || {};
    const levelEvents = Object.entries(strategy.level_events || {})
        .filter(([, event]) => Boolean(event))
        .map(([name, event]) =>
            `${name}: ${event.kind} at ${event.level?.name || "level"} ${formatNumber(event.level?.price)}`
        );
    const levelDistance = number(strategy.level_distance_pct);
    const positionAction = strategy.position_action || "";
    const requiredInputsAvailable =
        strategy.entry_filters?.required_data === true;
    const nifty15mCandles =
        data.indices?.["Nifty 50"]?.charts?.["15m"] || [];
    const lastNifty15mCandle = nifty15mCandles.reduce((latest, candle) => {
        const time = number(candle.time);
        return time !== null && (latest === null || time > latest)
            ? time
            : latest;
    }, null);
    const lastNifty15mClose = lastNifty15mCandle === null
        ? null
        : formatDashboardDateTime(
            new Date((lastNifty15mCandle + 15 * 60) * 1000).toISOString()
        );
    const freshnessMessage = marketClosed
        ? `Market closed — showing the latest available session data. Last completed Nifty 15m candle: ${lastNifty15mClose || "unavailable"} IST. No live entry signal is available after hours.`
        : !requiredInputsAvailable
            ? "Main strategy requires all market-data gates. The separate scalp signal uses fresh Nifty candles and live ATM quotes."
            : "Market open — entries still require fresh completed candles, option-chain confirmation, and every strategy gate to pass.";

    decision.innerHTML = `
        <h3>Nifty ATM Strategy Decision</h3>
        <p class="market-data-note" role="status">${escapeHtml(freshnessMessage)}</p>
        <div class="${signalClass}" style="font-size:1.35rem;font-weight:700;margin:12px 0">
            ${escapeHtml(signal)}
        </div>
        <div class="summary-row">
            <span>Scalp opportunity (signal only)</span>
            <strong class="${scalpClass}">${escapeHtml(scalpDecision)}</strong>
        </div>
        <p class="muted small-text">${escapeHtml(scalpReason)}</p>
        <div class="summary-row">
            <span>Decision time (IST)</span>
            <strong>${escapeHtml(formatDashboardDateTime(strategy.timestamp_ist || ""))}</strong>
        </div>
        <div class="summary-row">
            <span>Current exchange lot size</span>
            <strong>${formatNumber(strategy.lot_size, 0)}</strong>
        </div>
        <div class="summary-row">
            <span>CE / PE / net points</span>
            <strong>${formatNumber(strategy.ce_points, 1)} / ${formatNumber(strategy.pe_points, 1)} / ${formatNumber(strategy.net_score, 1)}</strong>
        </div>
        <div class="summary-row">
            <span>3m trigger</span>
            <strong>${escapeHtml(strategy.trigger_3m || "Unavailable")}</strong>
        </div>
        <div class="summary-row">
            <span>Nifty 5m / 10m / 15m</span>
            <strong>${escapeHtml(strategy.trends?.["Nifty 50"]?.["5m"] || "--")} / ${escapeHtml(strategy.trends?.["Nifty 50"]?.["10m"] || "--")} / ${escapeHtml(strategy.trends?.["Nifty 50"]?.["15m"] || "--")}</strong>
        </div>
        <div class="summary-row">
            <span>OI verdict / PCR / flow</span>
            <strong>${escapeHtml(oi.verdict || "Unavailable")} / ${escapeHtml(pcrDisplay)} / ${escapeHtml(oi.flow || "Unavailable")}</strong>
        </div>
        <div class="summary-row">
            <span>Nearest major level</span>
            <strong>${escapeHtml(level.name || "Unavailable")} ${formatNumber(level.price)}${levelDistance === null ? "" : ` (${levelDistance.toFixed(2)}%)`}</strong>
        </div>
        <p><strong>15m level confirmations:</strong> ${escapeHtml(levelEvents.join("; ") || "None")}</p>
        <p><strong>Matched setups:</strong> ${escapeHtml(setups.map(item => `${item.name} → ${item.direction} +${item.points}`).join("; ") || "None")}</p>
        <p><strong>Reason:</strong> ${escapeHtml(reasons.join(" ") || "No reason provided.")}</p>
        ${positionAction ? `<p><strong>Position action:</strong> ${escapeHtml(positionAction)}</p>` : ""}
        ${position.direction ? `
            <div class="summary-row">
                <span>Tracked reference (not broker-confirmed)</span>
                <strong>${escapeHtml(position.direction)} · ${formatNumber(position.quantity, 0)} units</strong>
            </div>
            <div class="summary-row">
                <span>Entry / current premium</span>
                <strong>${formatNumber(position.entry_premium)} / ${formatNumber(position.current_premium)}</strong>
            </div>
            <div class="summary-row">
                <span>Stop / target</span>
                <strong>${formatNumber(position.stop_premium)} / ${formatNumber(position.target_premium)}</strong>
            </div>
        ` : ""}
        <p class="muted small-text">
            Signal-only strategy; no broker orders are placed. Tracked positions are reference simulations.
        </p>
    `;
}
// ---------- Interactive charts ----------
// Compatible with the Lightweight Charts 4.2.2 library loaded by index.html.

function chartTime(candle) {
    const t = number(candle?.time);
    return t === null ? null : t;
}

function formatChartTime(time, options) {
    const timestamp = number(time);
    if (timestamp === null) return "";

    return new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        ...options
    }).format(new Date(timestamp * 1000));
}

function formatChartTick(time, tickMarkType) {
    const types = window.LightweightCharts?.TickMarkType || {};

    if (tickMarkType === types.Year) {
        return formatChartTime(time, { year: "numeric" });
    }
    if (tickMarkType === types.Month) {
        return formatChartTime(time, { month: "short", year: "numeric" });
    }
    if (tickMarkType === types.DayOfMonth) {
        return formatChartTime(time, { day: "numeric", month: "short" });
    }
    if (tickMarkType === types.TimeWithSeconds) {
        return formatChartTime(time, {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hourCycle: "h23"
        });
    }

    return formatChartTime(time, {
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23"
    });
}

function validCandles(candles) {
    if (!Array.isArray(candles)) return [];

    return candles
        .filter(c =>
            chartTime(c) !== null &&
            number(c.open) !== null &&
            number(c.high) !== null &&
            number(c.low) !== null &&
            number(c.close) !== null
        )
        .map(c => ({ ...c, time: chartTime(c) }))
        .sort((a, b) => a.time - b.time)
        .filter((c, i, arr) => i === 0 || c.time > arr[i - 1].time);
}

function clearCharts() {
    state.charts.forEach(chart => {
        try {
            chart.__resizeObserver?.disconnect();
            chart.remove();
        } catch (error) {
            console.warn("Chart cleanup warning:", error);
        }
    });

    state.charts = [];
}

function addChart(container, options = {}) {
    if (!container || !window.LightweightCharts) return null;

    container.innerHTML = "";

    const chart = window.LightweightCharts.createChart(container, {
        width: Math.max(280, container.clientWidth || 280),
        height: options.height || 240,

        layout: {
            background: { color: "transparent" },
            textColor: "#94a3b8",
            fontFamily: "DM Sans, sans-serif"
        },

        localization: {
            timeFormatter: time => `${formatChartTime(time, {
                day: "numeric",
                month: "short",
                year: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
                hourCycle: "h23"
            })} IST`
        },

        grid: {
            vertLines: { color: "rgba(148,163,184,0.10)" },
            horzLines: { color: "rgba(148,163,184,0.10)" }
        },

        rightPriceScale: {
            borderColor: "rgba(148,163,184,0.20)"
        },

        timeScale: {
            borderColor: "rgba(148,163,184,0.20)",
            timeVisible: options.timeVisible !== false,
            secondsVisible: false,
            tickMarkFormatter: formatChartTick
        },

        crosshair: { mode: 0 },
        handleScroll: true,
        handleScale: true
    });

    state.charts.push(chart);

    if (window.ResizeObserver) {
        const observer = new ResizeObserver(() => {
            if (container.clientWidth > 0) {
                chart.applyOptions({
                    width: container.clientWidth
                });
            }
        });

        observer.observe(container);
        chart.__resizeObserver = observer;
    }

    return chart;
}

function syncChartTimeScales() {
    if (state.charts.length < 2) return;

    const master = state.charts[0];

    master.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (!range || state.syncingCharts) return;

        state.syncingCharts = true;

        state.charts.slice(1).forEach(chart => {
            try {
                chart.timeScale().setVisibleLogicalRange(range);
            } catch (_) {
                // Ignore synchronization errors while charts initialize.
            }
        });

        state.syncingCharts = false;
    });
}

function renderChart() {
    const data = state.data;
    if (!data) return;

    const indexData = data.indices?.[state.selectedIndex];

    if (!indexData) {
        setText("chartInstrument", state.selectedIndex);
        setText("chartPrice", "--");
        setText("chartChange", "No data");
        return;
    }

    const timeframe = state.selectedTimeframe;
    const timeframeData = indexData.timeframes?.[timeframe] || {};

    const candles = validCandles(
        indexData.charts?.[timeframe] || []
    );

    setText(
        "chartInstrument",
        `${state.selectedIndex} · ${timeframe}`
    );

    const ltp = number(
        indexData.ltp,
        number(timeframeData.ltp, number(timeframeData.close))
    );
    setText("chartPrice", formatNumber(ltp));

    const change = number(timeframeData.chg, 0);

    setText("chartChange", formatPercent(change));

    const changeElement = $("chartChange");

    if (changeElement) {
        changeElement.classList.toggle("positive", change >= 0);
        changeElement.classList.toggle("negative", change < 0);
    }

    setText("candleCount", `${candles.length} candles`);

    clearCharts();

    const priceContainer = $("priceChart");
    const volumeContainer = $("volumeChart");
    const rsiContainer = $("rsiChart");

    if (!candles.length) {
        if (priceContainer) {
            priceContainer.innerHTML =
                '<div class="chart-placeholder">No candle data available for this timeframe.</div>';
        }

        if (volumeContainer) {
            volumeContainer.innerHTML =
                '<div class="chart-placeholder">No volume data available.</div>';
        }

        if (rsiContainer) {
            rsiContainer.innerHTML =
                '<div class="chart-placeholder">No RSI data available.</div>';
        }

        return;
    }

    if (!window.LightweightCharts) {
        if (priceContainer) {
            priceContainer.innerHTML =
                '<div class="chart-placeholder">Chart library unavailable. Refresh the page or check your connection.</div>';
        }
        return;
    }

    const priceChart = addChart(priceContainer, { height: 300 });
    if (!priceChart) return;

    const candleSeries = priceChart.addCandlestickSeries({
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderUpColor: "#22c55e",
        borderDownColor: "#ef4444",
        wickUpColor: "#22c55e",
        wickDownColor: "#ef4444"
    });

    candleSeries.setData(
        candles.map(c => ({
            time: c.time,
            open: Number(c.open),
            high: Number(c.high),
            low: Number(c.low),
            close: Number(c.close)
        }))
    );

    if (ltp !== null) {
        candleSeries.createPriceLine({
            price: ltp,
            color: "#f8fafc",
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: "LTP"
        });
    }

    const movingAverages = [
        { key: "sma20", name: "SMA 20", color: "#38bdf8" },
        { key: "sma50", name: "SMA 50", color: "#f59e0b" },
        { key: "sma100", name: "SMA 100", color: "#a78bfa" }
    ];

    movingAverages.forEach(def => {
        const values = candles
            .filter(c => number(c[def.key]) !== null)
            .map(c => ({
                time: c.time,
                value: Number(c[def.key])
            }));

        if (!values.length) return;

        const series = priceChart.addLineSeries({
            color: def.color,
            lineWidth: 2,
            title: def.name,
            priceLineVisible: false,
            lastValueVisible: false
        });

        series.setData(values);
    });

    // Volume chart
    const volumeChart = addChart(volumeContainer, {
        height: 110,
        timeVisible: true
    });

    if (volumeChart) {
        const volumeSeries = volumeChart.addHistogramSeries({
            priceFormat: { type: "volume" },
            priceScaleId: ""
        });

        volumeSeries.setData(
            candles.map(c => ({
                time: c.time,
                value: Math.max(0, number(c.volume, 0)),
                color: Number(c.close) >= Number(c.open)
                    ? "rgba(34,197,94,0.65)"
                    : "rgba(239,68,68,0.65)"
            }))
        );
    }

    // RSI chart
    const rsiChart = addChart(rsiContainer, {
        height: 110,
        timeVisible: true
    });

    if (rsiChart) {
        const rsiValues = candles
            .filter(c => number(c.rsi) !== null)
            .map(c => ({
                time: c.time,
                value: Number(c.rsi)
            }));

        if (rsiValues.length) {
            const rsiSeries = rsiChart.addLineSeries({
                color: "#c084fc",
                lineWidth: 2,
                title: "RSI",
                priceLineVisible: false
            });

            rsiSeries.setData(rsiValues);

            rsiChart.priceScale("right").applyOptions({
                autoScale: true,
                scaleMargins: { top: 0.1, bottom: 0.1 }
            });

            rsiChart.applyOptions({
                rightPriceScale: {
                    scaleMargins: { top: 0.1, bottom: 0.1 }
                }
            });
        } else if (rsiContainer) {
            rsiContainer.innerHTML =
                '<div class="chart-placeholder">RSI data is not available for these candles.</div>';
        }
    }

    // Show the latest 60 candles initially; older candles remain accessible by zooming out.
    const visibleRange = {
        from: Math.max(0, candles.length - 60),
        to: candles.length + 1
    };

    state.charts.forEach(chart => {
        try {
            chart.timeScale().setVisibleLogicalRange(visibleRange);
        } catch (_) {
            chart.timeScale().fitContent();
        }
    });

    syncChartTimeScales();
}
// ---------- Previous Analyses ----------

async function renderHistory() {
    const container = $("historyList");
    if (!container) return;

    try {
        const history = await fetchJson(HISTORY_INDEX_URL);

        state.history = Array.isArray(history) ? history : [];

        if (!state.history.length) {
            container.innerHTML =
                '<div class="loading-card">No historical snapshots are available.</div>';
            return;
        }

        container.innerHTML = state.history.map((item, index) => {
            const filename = String(item.file || "");

            // Allow only a simple JSON filename.
            const safeFilename = /^[a-zA-Z0-9._-]+\.json$/.test(filename);

            const date =
                item.generated_at_ist ||
                item.generated_at ||
                filename;

            return `
                <div class="history-item">
                    <div class="history-item-copy">
                        <strong>${escapeHtml(formatDashboardDateTime(date))}</strong>
                        <span class="muted small-text">
                            Score: ${formatNumber(item.score, 2)} ·
                            Nifty: ${formatNumber(item.nifty_close)}
                        </span>
                        <span class="status-pill ${sentimentClass(item.label)}">
                            ${escapeHtml(item.label || "No label")}
                        </span>
                    </div>

                    ${safeFilename
                        ? `<button class="text-button history-view-button"
                            type="button"
                            data-history-index="${index}">
                            Inspect snapshot
                        </button>`
                        : ""}
                </div>
            `;
        }).join("");

        container.querySelectorAll("[data-history-index]").forEach(button => {
            button.addEventListener("click", async () => {
                const item = state.history[
                    Number(button.dataset.historyIndex)
                ];

                if (
                    !item ||
                    !/^[a-zA-Z0-9._-]+\.json$/.test(String(item.file || ""))
                ) {
                    return;
                }

                button.disabled = true;

                try {
                    // The index is in data/history, but snapshots are in data/snapshots.
                    const snapshot = await fetchJson(
                        `${SNAPSHOT_BASE_URL}${encodeURIComponent(item.file)}`
                    );

                    renderDashboard(snapshot);

                    window.scrollTo({
                        top: 0,
                        behavior: "smooth"
                    });

                    showToast(
                        "Historical snapshot loaded. Refresh to return to latest data."
                    );
                } catch (error) {
                    console.error(
                        "Could not load historical snapshot:",
                        error
                    );

                    showToast(
                        "Could not load this snapshot. Check that the file exists in data/snapshots/."
                    );
                } finally {
                    button.disabled = false;
                }
            });
        });
    } catch (error) {
        console.error("History loading failed:", error);

        container.innerHTML =
            '<div class="loading-card">Historical data could not be loaded. Refresh the page to retry.</div>';
    }
}

// ---------- DEV dashboard rendering ----------

function renderDashboard(data) {
    if (!data || typeof data !== "object" || !data.indices) {
        throw new Error(
            "latest.json is not a valid Market Pulse data object."
        );
    }

    state.data = data;

    renderOverall(data);
    renderIndexCards(data);
    renderTechnicalCards(data);
    renderWeeklyCards(data);
    renderOptions(data);
    renderPriceActionDecision(data);
    renderCombinedDecision(data);
    renderGlobalMarkets(data);
    renderBreakdown(data);
    renderNews(data);
    renderChart();
}

async function loadDashboard(showMessage = false) {
    if (state.loading) return;

    state.loading = true;

    const refreshButton = $("refreshButton");

    if (refreshButton) {
        refreshButton.disabled = true;
        refreshButton.classList.add("loading");
    }

    setText("dataStatus", "Loading data");

    try {
        const data = await fetchJson(DATA_URL);

        renderDashboard(data);

        if (showMessage) {
            showToast("Latest market data loaded.");
        }
    } catch (error) {
        console.error("Dashboard loading failed:", error);

        setText("dataStatus", "Data unavailable");

        if (!state.data) {
            setText("overallLabel", "Unable to load market data");

            setText(
                "scoreNote",
                "Could not load latest.json. Check the file URL and try refreshing."
            );

            setHtml(
                "indexCards",
                '<div class="loading-card">Unable to load market data. Please refresh.</div>'
            );
        }

        if (showMessage) {
            showToast("Refresh failed. Please try again.");
        }
    } finally {
        state.loading = false;

        if (refreshButton) {
            refreshButton.disabled = false;
            refreshButton.classList.remove("loading");
        }
    }
}

// ---------- Controls ----------

function setupControls() {
    const detailCards = $("detailCards");
    const detailToggle = $("toggleDetailCards");
    if (detailCards && detailToggle) {
        detailToggle.addEventListener("click", () => {
            const expanded = detailToggle.getAttribute("aria-expanded") === "true";
            detailCards.hidden = expanded;
            detailToggle.setAttribute("aria-expanded", String(!expanded));
            detailToggle.textContent = expanded
                ? "Show detailed cards"
                : "Hide detailed cards";
        });
    }

    const indexSelect = $("indexSelect");

    if (indexSelect) {
        indexSelect.addEventListener("change", () => {
            state.selectedIndex = indexSelect.value;
            renderChart();
        });
    }

    document.querySelectorAll("[data-timeframe]").forEach(button => {
        button.addEventListener("click", () => {
            state.selectedTimeframe = button.dataset.timeframe;

            document.querySelectorAll("[data-timeframe]").forEach(item => {
                item.classList.toggle("active", item === button);
            });

            renderChart();
        });
    });

    const refreshButton = $("refreshButton");

    if (refreshButton) {
        refreshButton.addEventListener("click", () => {
            loadDashboard(true);
            renderHistory();
        });
    }

    const backToTop = $("backToTop");

    if (backToTop) {
        backToTop.addEventListener("click", () => {
            window.scrollTo({
                top: 0,
                behavior: "smooth"
            });
        });
    }
}

// ---------- Start dashboard ----------

function startDashboard() {
    setupControls();

    loadDashboard(false);
    renderHistory();

    // Refresh the published JSON every five minutes.
    // This does not trigger a new GitHub Actions data collection.
    window.setInterval(() => {
        loadDashboard(false);
    }, 5 * 60 * 1000);
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startDashboard, {
        once: true
    });
} else {
    startDashboard();
}