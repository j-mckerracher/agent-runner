/*
 * Run Telemetry view.
 *
 * Contains filter state, KPI/table renderers, chart configuration, and polling.
 * Chart options are intentionally kept close to their renderers so future
 * changes to labels, axes, and data transforms can be made in one place.
 */

function telemetryActive() {
    return $('.view[data-view="telemetry"]')?.classList.contains(
        "active",
    );
}
function telemetryRangeBounds() {
    const range = $("#telemetry-range")?.value || "all";
    if (range === "custom") {
        return {
            submitted_after: $("#telemetry-start").value.trim(),
            submitted_before: $("#telemetry-end").value.trim(),
        };
    }
    if (range === "all")
        return { submitted_after: "", submitted_before: "" };
    const hours =
        range === "1h"
            ? 1
            : range === "7d"
              ? 24 * 7
              : range === "30d"
                ? 24 * 30
                : 24;
    return {
        submitted_after: new Date(
            Date.now() - hours * 3600 * 1000,
        ).toISOString(),
        submitted_before: "",
    };
}
function selectedOptions(select) {
    return [...(select?.selectedOptions || [])]
        .map((o) => o.value)
        .filter(Boolean);
}
function telemetryFilters() {
    const bounds = telemetryRangeBounds();
    return {
        status: selectedOptions($("#telemetry-status")),
        runner: $("#telemetry-runner").value
            ? [$("#telemetry-runner").value]
            : [],
        model: $("#telemetry-model").value
            ? [$("#telemetry-model").value]
            : [],
        run_kind: $("#telemetry-run-kind").value
            ? [$("#telemetry-run-kind").value]
            : [],
        mode: $("#telemetry-mode").value
            ? [$("#telemetry-mode").value]
            : [],
        repo: $("#telemetry-repo").value
            ? [$("#telemetry-repo").value]
            : [],
        change_id: $("#telemetry-change").value.trim(),
        submitted_after: bounds.submitted_after,
        submitted_before: bounds.submitted_before,
        q: $("#telemetry-q").value.trim(),
        stage: [],
        failed_stage: TELEMETRY_STATE.failedStage
            ? [TELEMETRY_STATE.failedStage]
            : [],
        min_tokens: null,
        min_cost_usd: null,
        errors_only: false,
        awaiting_input_only: false,
        missing_events_only: !!TELEMETRY_STATE.missingEventsOnly,
    };
}
function telemetryRunsQuery(filters) {
    const params = new URLSearchParams({
        limit: "500",
        offset: "0",
    });
    const scalar = {
        status: filters.status[0] || "",
        runner: filters.runner[0] || "",
        model: filters.model[0] || "",
        run_kind: filters.run_kind[0] || "",
        mode: filters.mode[0] || "",
        repo: filters.repo[0] || "",
        change_id: filters.change_id || "",
        submitted_after: filters.submitted_after || "",
        submitted_before: filters.submitted_before || "",
        q: filters.q || "",
    };
    Object.entries(scalar).forEach(([key, value]) => {
        if (value) params.set(key, value);
    });
    return `/telemetry/runs?${params.toString()}`;
}
function pickDefaultTelemetryRun(runs) {
    const items = Array.isArray(runs) ? runs.filter(Boolean) : [];
    if (!items.length) return null;
    return (
        items.find((row) => row.status === "running") ||
        items.find((row) => row.status === "awaiting_input") ||
        items.find((row) => row.status === "queued") ||
        items[0]
    );
}
function setSelectOptions(selector, values, anyLabel = "Any") {
    const select = $(selector);
    if (!select) return;
    const current = select.multiple
        ? selectedOptions(select)
        : [select.value];
    select.innerHTML = select.multiple
        ? ""
        : `<option value="">${escapeHtml(anyLabel)}</option>`;
    (values || []).forEach((value) => {
        const opt = document.createElement("option");
        opt.value = value;
        opt.textContent = value;
        if (current.includes(value)) opt.selected = true;
        select.appendChild(opt);
    });
}
function populateTelemetryOptions(options) {
    if (!options) return;
    setSelectOptions(
        "#telemetry-status",
        options.statuses || [],
        "Any",
    );
    setSelectOptions(
        "#telemetry-runner",
        options.runners || [],
        "Any",
    );
    setSelectOptions(
        "#telemetry-model",
        options.models || [],
        "Any",
    );
    setSelectOptions(
        "#telemetry-run-kind",
        options.run_kinds || [],
        "Any",
    );
    setSelectOptions("#telemetry-mode", options.modes || [], "Any");
    setSelectOptions("#telemetry-repo", options.repos || [], "Any");
}
function telemetryCostLabel(source) {
    if (source === "actual") return "actual";
    if (source === "estimated") return "estimated";
    if (source === "mixed") return "mixed";
    return "unknown";
}
function telemetryCostValue(rowOrSummary, source) {
    if (source === "unknown") return null;
    const actual = Number(rowOrSummary?.cost_usd);
    const estimated = Number(rowOrSummary?.estimated_cost_usd);
    if (
        source === "actual" &&
        Number.isFinite(actual) &&
        actual > 0
    )
        return actual;
    if (
        (source === "estimated" || source === "mixed") &&
        Number.isFinite(estimated) &&
        estimated > 0
    )
        return estimated;
    if (Number.isFinite(actual) && actual > 0) return actual;
    return Number.isFinite(estimated) && estimated > 0
        ? estimated
        : null;
}
function telemetryCostDisplay(rowOrSummary, source) {
    const value = telemetryCostValue(rowOrSummary, source);
    return value == null ? "—" : formatMoney(value);
}
function telemetryCoverageLabel(source) {
    if (source === "sqlite") return "SQLite telemetry";
    if (source === "jsonl_fallback") return "JSONL fallback";
    return "Telemetry gap";
}
function telemetrySafeNumber(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
}
function telemetryToneClass(tone) {
    return `tone-${tone || "neutral"}`;
}
function telemetryFailureTone(rate, count = 0) {
    const r = telemetrySafeNumber(rate) ?? 0;
    if (r >= 0.04 || Number(count || 0) >= 3) return "danger";
    if (r > 0 || Number(count || 0) > 0) return "warn";
    return "good";
}
function telemetrySuccessTone(rate) {
    const r = telemetrySafeNumber(rate);
    if (r == null) return "neutral";
    if (r < 0.85) return "danger";
    if (r < 0.95) return "warn";
    return "good";
}
function telemetryCostTone(cost, selectedCount) {
    const value = telemetrySafeNumber(cost);
    if (value == null || value <= 0) return "neutral";
    const perRun = selectedCount ? value / selectedCount : value;
    if (value >= 20 || perRun >= 1) return "danger";
    if (value >= 5 || perRun >= 0.25) return "warn";
    return "good";
}
function telemetryDurationTone(
    seconds,
    warnAt = 300,
    dangerAt = 1200,
) {
    const value = telemetrySafeNumber(seconds);
    if (value == null) return "neutral";
    if (value >= dangerAt) return "danger";
    if (value >= warnAt) return "warn";
    return "good";
}
function telemetryBadgeClass(label) {
    const text = String(label || "").toLowerCase();
    if (
        text.includes("token") ||
        text.includes("queued") ||
        text.includes("await")
    )
        return "warn";
    if (
        text.includes("fail") ||
        text.includes("error") ||
        text.includes("timeout")
    )
        return "error";
    return "info";
}
function renderAttentionBadges(badges) {
    const visible = (badges || []).filter(
        (b) =>
            String(b || "").toLowerCase() !==
            "no structured events",
    );
    return visible
        .map(
            (b) =>
                `<span class="telemetry-badge ${telemetryBadgeClass(b)}">${escapeHtml(b)}</span>`,
        )
        .join("");
}
function renderCoverageBadge(source, status) {
    if (source === "none") {
        return status === "failed"
            ? `<span class="telemetry-badge muted">telemetry gap</span>`
            : "";
    }
    const cls = source === "jsonl_fallback" ? "warn" : "ok";
    return `<span class="telemetry-badge ${cls}">${escapeHtml(telemetryCoverageLabel(source))}</span>`;
}
function telemetryMetricPill(value, tone) {
    return `<span class="telemetry-metric-pill ${telemetryToneClass(tone)}">${escapeHtml(value)}</span>`;
}
function renderStageDuration(value, warnAt = 300, dangerAt = 1200) {
    const tone = telemetryDurationTone(value, warnAt, dangerAt);
    return `<span class="telemetry-emphasis ${telemetryToneClass(tone)}">${escapeHtml(formatSeconds(value))}</span>`;
}
function renderFailureRatePill(rate, count) {
    return telemetryMetricPill(
        formatPercent(rate),
        telemetryFailureTone(rate, count),
    );
}
function telemetryErrorSeverity(row) {
    const explicit = String(row?.severity || "").toLowerCase();
    if (["critical", "high", "medium", "low"].includes(explicit))
        return explicit;
    const count = Number(row?.count || 0);
    const text =
        `${row?.sample_message || ""} ${row?.signature || ""}`.toLowerCase();
    if (
        count >= 10 ||
        text.includes("out of memory") ||
        text.includes("permission denied")
    )
        return "critical";
    if (
        count >= 3 ||
        row?.recurring ||
        text.includes("timeout") ||
        text.includes("rate limit")
    )
        return "high";
    if (count >= 1) return "medium";
    return "low";
}
function telemetryErrorSeverityScore(row) {
    const explicit = Number(row?.severity_score);
    if (Number.isFinite(explicit)) return explicit;
    return (
        { critical: 4, high: 3, medium: 2, low: 1 }[
            telemetryErrorSeverity(row)
        ] || 0
    );
}
function telemetrySeverityTone(severity) {
    if (severity === "critical" || severity === "high")
        return "danger";
    if (severity === "medium") return "warn";
    return "info";
}
function sortTelemetryErrors(rows) {
    const ts = (value) => Date.parse(value || "") || 0;
    return [...(rows || [])].sort(
        (a, b) =>
            telemetryErrorSeverityScore(b) -
                telemetryErrorSeverityScore(a) ||
            Number(b.count || 0) - Number(a.count || 0) ||
            ts(b.last_seen) - ts(a.last_seen),
    );
}
function telemetryKpiCard(card) {
    const tone = telemetryToneClass(card.tone);
    const hero = card.hero ? "is-hero" : "";
    const unit = card.unit
        ? `<span class="telemetry-kpi-unit">${escapeHtml(card.unit)}</span>`
        : "";
    const source = card.source
        ? `<div class="telemetry-card-source">${escapeHtml(card.source)}</div>`
        : "";
    return `
    <div class="card telemetry-kpi-card ${hero} ${tone}" data-telemetry-card="${escapeHtml(card.action || card.label)}">
      <div class="lbl">${escapeHtml(card.label)}</div>
      <div class="telemetry-kpi-main"><div class="val">${escapeHtml(card.value)}</div>${unit}</div>
      ${card.sub ? `<div class="telemetry-kpi-sub">${escapeHtml(card.sub)}</div>` : ""}
      ${source}
    </div>`;
}
function renderTelemetryCards(data) {
    const summary = data?.summary || {};
    const coverage = data?.coverage || {};
    const selectedCount = Number(data?.selected_count || 0);
    const costValue = telemetryCostValue(
        summary,
        coverage.cost_data_source,
    );
    const costPerRun =
        costValue != null && selectedCount
            ? costValue / selectedCount
            : null;
    const groups = [
        {
            key: "volume",
            title: "Volume",
            cards: [
                {
                    label: "Selected runs",
                    value: formatNumber(selectedCount),
                    unit: "runs",
                    hero: true,
                    tone: "info",
                    sub: `${coverage.aggregated_jobs || 0}/${coverage.matched_jobs ?? coverage.aggregated_jobs ?? 0} aggregated`,
                },
                {
                    label: "Active now",
                    value: formatNumber(summary.active || 0),
                    unit: "live",
                    tone:
                        (summary.active || 0) > 0
                            ? "info"
                            : "neutral",
                    sub: "Running pipelines",
                },
                {
                    label: "Queued",
                    value: formatNumber(summary.queued || 0),
                    unit: "queued",
                    tone:
                        (summary.queued || 0) > 0
                            ? "warn"
                            : "neutral",
                    sub: "Waiting to start",
                },
                {
                    label: "Awaiting input",
                    value: formatNumber(
                        summary.awaiting_input || 0,
                    ),
                    unit: "blocked",
                    tone:
                        (summary.awaiting_input || 0) > 0
                            ? "warn"
                            : "good",
                    sub: "User action needed",
                    action: "Awaiting input",
                },
            ],
        },
        {
            key: "health",
            title: "Health",
            cards: [
                {
                    label: "Failure rate",
                    value: formatPercent(summary.failure_rate),
                    unit: "failed",
                    hero: true,
                    tone: telemetryFailureTone(
                        summary.failure_rate,
                    ),
                    sub: "Green 0%, yellow >0%, red ≥4%",
                    action: "Failure rate",
                },
                {
                    label: "Success rate",
                    value: formatPercent(summary.success_rate),
                    unit: "success",
                    tone: telemetrySuccessTone(
                        summary.success_rate,
                    ),
                    sub: "Target ≥95%",
                },
                {
                    label: "Top failed stage",
                    value: summary.top_failed_stage || "—",
                    unit: summary.top_failed_stage ? "stage" : "",
                    tone: summary.top_failed_stage
                        ? "danger"
                        : "good",
                    sub: summary.top_failed_stage
                        ? "Click to isolate failed runs"
                        : "No dominant failed stage",
                    action: "Top failed stage",
                },
            ],
        },
        {
            key: "performance",
            title: "Performance",
            cards: [
                {
                    label: "Median runtime",
                    value: formatSeconds(
                        summary.median_runtime_seconds,
                    ),
                    unit: "median",
                    hero: true,
                    tone: telemetryDurationTone(
                        summary.median_runtime_seconds,
                        300,
                        1200,
                    ),
                    sub: "Green <5m, yellow ≥5m, red ≥20m",
                },
                {
                    label: "P95 runtime",
                    value: formatSeconds(
                        summary.p95_runtime_seconds,
                    ),
                    unit: "p95",
                    tone: telemetryDurationTone(
                        summary.p95_runtime_seconds,
                        1200,
                        3600,
                    ),
                    sub: "Tail latency",
                },
                {
                    label: "Median queue",
                    value: formatSeconds(
                        summary.median_queue_seconds,
                    ),
                    unit: "queue",
                    tone: telemetryDurationTone(
                        summary.median_queue_seconds,
                        60,
                        300,
                    ),
                    sub: "Scheduling delay",
                },
                {
                    label: "User-blocked time",
                    value: formatSeconds(
                        summary.user_blocked_seconds,
                    ),
                    unit: "blocked",
                    tone: telemetryDurationTone(
                        summary.user_blocked_seconds,
                        300,
                        1200,
                    ),
                    sub: "Human wait time",
                },
            ],
        },
        {
            key: "cost",
            title: "Cost",
            cards: [
                {
                    label: "Cost",
                    value: telemetryCostDisplay(
                        summary,
                        coverage.cost_data_source,
                    ),
                    unit: "usd",
                    hero: true,
                    tone: telemetryCostTone(
                        costValue,
                        selectedCount,
                    ),
                    sub:
                        costPerRun == null
                            ? "No cost signal"
                            : `${formatMoney(costPerRun)} per run`,
                    source: telemetryCostLabel(
                        coverage.cost_data_source,
                    ),
                },
                {
                    label: "Total tokens",
                    value: formatNumber(summary.tokens_total),
                    unit: "tokens",
                    tone:
                        (summary.tokens_total || 0) >= 100000
                            ? "warn"
                            : "neutral",
                    sub: "Input + output",
                },
            ],
        },
    ];
    $("#telemetry-cards").innerHTML = groups
        .map(
            (group) => `
    <section class="telemetry-kpi-group is-${escapeHtml(group.key)}" aria-label="${escapeHtml(group.title)} KPIs">
      <div class="telemetry-kpi-group-label">${escapeHtml(group.title)}</div>
      <div class="telemetry-kpi-group-grid">${group.cards.map(telemetryKpiCard).join("")}</div>
    </section>`,
        )
        .join("");
    $$("[data-telemetry-card]").forEach((card) => {
        card.addEventListener("click", () => {
            const label = card.dataset.telemetryCard;
            TELEMETRY_STATE.missingEventsOnly = false;
            if (label === "Failure rate") {
                $("#telemetry-status").value = "failed";
                TELEMETRY_STATE.failedStage = null;
            }
            if (label === "Awaiting input") {
                $("#telemetry-status").value = "awaiting_input";
                TELEMETRY_STATE.failedStage = null;
            }
            if (
                label === "Top failed stage" &&
                summary.top_failed_stage
            ) {
                $("#telemetry-status").value = "failed";
                TELEMETRY_STATE.failedStage =
                    summary.top_failed_stage;
            }
            scheduleTelemetryRefresh();
        });
    });
}
function emptyTelemetryRow(cols, message = "No data") {
    return `<tr><td colspan="${cols}" class="empty">${escapeHtml(message)}</td></tr>`;
}
function renderTelemetryTable(tbodySelector, rows, cols, render) {
    const tbody = $(tbodySelector);
    if (!tbody) return;
    tbody.innerHTML = rows?.length
        ? rows.map(render).join("")
        : emptyTelemetryRow(cols);
}
function renderTelemetryAggregate(data) {
    TELEMETRY_STATE.aggregate = data;
    renderTelemetryCards(data);
    const coverage = data.coverage || {};
    const note = $("#telemetry-coverage-note");
    const coverageText = `Structured event data available for ${coverage.jobs_with_events || 0} of ${data.selected_count || 0} selected runs. Cost source: ${telemetryCostLabel(coverage.cost_data_source)}.${coverage.truncated ? ` Aggregation truncated: ${coverage.aggregated_jobs} of ${coverage.matched_jobs} matched jobs.` : ""}`;
    note.textContent = coverageText;
    note.style.display =
        coverage.jobs_missing_events ||
        coverage.truncated ||
        coverage.cost_data_source !== "actual"
            ? "block"
            : "none";
    $("#telemetry-meta").textContent =
        `${data.selected_count || 0} selected · refreshed ${fmtLocalTime(new Date().toISOString())}`;
    $("#telemetry-selection-meta").textContent =
        TELEMETRY_STATE.selectionMode === "all"
            ? "All matching runs"
            : `${TELEMETRY_STATE.selectedIds.size} selected run${TELEMETRY_STATE.selectedIds.size === 1 ? "" : "s"}`;
    renderTelemetryTable(
        "#telemetry-live",
        data.active_runs || [],
        9,
        (row) => `
    <tr><td><span class="status ${escapeHtml(row.status)}">${escapeHtml(row.status)}</span></td><td>${escapeHtml(row.change_id || "")}</td><td class="mono">${escapeHtml(row.repo || "")}</td><td>${escapeHtml(row.runner || "")}/${escapeHtml(row.model || "")}</td><td>${escapeHtml(row.current_stage || "—")}</td><td>${formatSeconds(row.duration_seconds)}</td><td>${formatNumber((row.tokens_in || 0) + (row.tokens_out || 0))}</td><td>${telemetryCostDisplay(row, row.cost_source)} <span class="telemetry-badge muted">${escapeHtml(telemetryCostLabel(row.cost_source))}</span></td><td>${renderAttentionBadges(row.attention_badges)}</td></tr>`,
    );
    renderTelemetryTable(
        "#telemetry-stages",
        data.stage_stats || [],
        9,
        (row) => {
            const failureTone = telemetryFailureTone(
                row.failure_rate,
                row.failure_count,
            );
            const activeCell = row.active_count
                ? `<span class="telemetry-badge warn">${formatNumber(row.active_count)} active</span>`
                : formatNumber(row.active_count);
            return `<tr class="${failureTone === "danger" ? "needs-attention" : ""}"><td>${escapeHtml(row.stage)}</td><td>${formatNumber(row.runs_observed)}</td><td>${activeCell}</td><td>${renderStageDuration(row.median_duration_seconds, 300, 1200)}</td><td>${renderStageDuration(row.p95_duration_seconds, 600, 1800)}</td><td>${renderStageDuration(row.p99_duration_seconds ?? row.p95_duration_seconds, 600, 1800)}</td><td>${formatSeconds(row.total_duration_seconds)}</td><td>${formatNumber(row.failure_count)}</td><td>${renderFailureRatePill(row.failure_rate, row.failure_count)}</td></tr>`;
        },
    );
    renderTelemetryTable(
        "#telemetry-errors",
        sortTelemetryErrors(data.error_stats || []),
        7,
        (row) => {
            const severity = telemetryErrorSeverity(row);
            const tone = telemetrySeverityTone(severity);
            const triage = row.triage_state || "open";
            const recurring = row.recurring
                ? `<span class="telemetry-badge warn">recurring</span>`
                : "";
            return `<tr class="${tone === "danger" ? "needs-attention" : ""}"><td>${telemetryMetricPill(severity.toUpperCase(), tone)}</td><td><span class="telemetry-triage">${escapeHtml(triage)}</span>${recurring}</td><td><div class="telemetry-signature">${escapeHtml(row.signature)}</div><div class="telemetry-error-message">${escapeHtml(row.sample_message || "No sample message captured.")}</div></td><td>${telemetryMetricPill(formatNumber(row.count), tone)}</td><td>${escapeHtml(fmtLocalTime(row.last_seen))}</td><td>${escapeHtml(row.affected_stage || "—")}</td><td class="mono">${escapeHtml(row.sample_job_id || "")}<br>${escapeHtml(row.sample_change_id || "")}<br>${escapeHtml(row.runner_model || "—")}</td></tr>`;
        },
    );
    const ui = data.user_input_stats || {};
    $("#telemetry-user-summary").textContent =
        `${ui.total_prompt_count || 0} prompts · ${ui.timeout_count || 0} timeouts · ${formatSeconds(ui.total_blocked_seconds)} blocked · common stage ${ui.most_common_prompting_stage || "—"}`;
    renderTelemetryTable(
        "#telemetry-prompts",
        ui.active_unresolved_prompts || [],
        7,
        (row) => `
    <tr><td class="mono">${escapeHtml(row.job_id || "")}</td><td>${escapeHtml(row.change_id || "")}</td><td>${escapeHtml(row.stage || "—")}</td><td>${escapeHtml(row.agent || "—")}</td><td>${escapeHtml(row.severity || "—")}</td><td>${escapeHtml(row.title || "")}</td><td>${formatSeconds(row.age_seconds)}</td></tr>`,
    );
    renderTelemetryTable(
        "#telemetry-runner-models",
        data.runner_model_stats || [],
        9,
        (row) => `
    <tr><td>${escapeHtml(row.runner_model || "")}</td><td>${formatNumber(row.runs)}</td><td>${formatPercent(row.success_rate)}</td><td>${formatSeconds(row.median_runtime_seconds)}</td><td>${formatSeconds(row.p95_runtime_seconds)}</td><td>${formatNumber(row.tokens_per_run)}</td><td>${formatMoney(row.cost_per_run)}</td><td>${row.cost_per_successful_run == null ? "—" : formatMoney(row.cost_per_successful_run)}</td><td>${escapeHtml(row.most_common_failure || "—")}</td></tr>`,
    );
    renderTelemetryCharts(data.charts || {});
    renderTelemetryRuns(data.run_rows || []);
    renderTelemetryTimeseries(data.timeseries || []);
}
function sortTelemetryRows(rows) {
    const sort = $("#telemetry-sort")?.value || "newest";
    const n = (value) => Number(value) || 0;
    const ts = (value) => Date.parse(value || "") || 0;
    return [...rows].sort((a, b) => {
        if (sort === "slowest")
            return n(b.duration_seconds) - n(a.duration_seconds);
        if (sort === "expensive")
            return (
                n(b.cost_usd || b.estimated_cost_usd) -
                n(a.cost_usd || a.estimated_cost_usd)
            );
        if (sort === "tokens")
            return (
                n((b.tokens_in || 0) + (b.tokens_out || 0)) -
                n((a.tokens_in || 0) + (a.tokens_out || 0))
            );
        if (sort === "failed")
            return (
                (b.status === "failed") - (a.status === "failed") ||
                ts(b.submitted_at) - ts(a.submitted_at)
            );
        if (sort === "awaiting")
            return (
                (b.status === "awaiting_input") -
                    (a.status === "awaiting_input") ||
                n(b.duration_seconds) - n(a.duration_seconds)
            );
        if (sort === "error")
            return (
                (b.error_summary ? 1 : 0) -
                    (a.error_summary ? 1 : 0) ||
                ts(b.submitted_at) - ts(a.submitted_at)
            );
        return ts(b.submitted_at) - ts(a.submitted_at);
    });
}
function renderTelemetryRuns(rows) {
    const sorted = sortTelemetryRows(rows);
    const tbody = $("#telemetry-runs");
    if (!tbody) return;
    tbody.innerHTML = sorted.length
        ? sorted
              .map((row) => {
                  const selected = TELEMETRY_STATE.selectedIds.has(
                      row.id,
                  );
                  const coverageBadge = renderCoverageBadge(
                      row.coverage_source,
                      row.status,
                  );
                  const errorCell = [
                      escapeHtml(row.error_summary || ""),
                      coverageBadge,
                  ]
                      .filter(Boolean)
                      .join("<br>");
                  return `<tr class="${selected ? "is-selected" : ""}" data-telemetry-job="${escapeHtml(row.id)}">
      <td><input type="checkbox" class="telemetry-run-check" data-job-id="${escapeHtml(row.id)}" ${selected ? "checked" : ""}></td>
      <td>${escapeHtml(fmtLocalTime(row.submitted_at))}</td><td>${formatSeconds(row.duration_seconds)}</td><td>${formatSeconds(row.queue_seconds)}</td>
      <td>${escapeHtml(row.change_id || "")}</td><td class="mono">${escapeHtml(row.repo || "")}</td><td>${escapeHtml(row.run_kind || "")}</td>
      <td>${escapeHtml(row.runner || "")}</td><td>${escapeHtml(row.model || "")}</td><td>${escapeHtml(row.mode || "")}</td>
      <td><span class="status ${escapeHtml(row.status)}">${escapeHtml(row.status || "")}</span></td><td>${escapeHtml(row.current_stage || row.failed_stage || "—")}</td>
      <td>${formatNumber((row.tokens_in || 0) + (row.tokens_out || 0))}</td><td>${telemetryCostDisplay(row, row.cost_source)} <span class="telemetry-badge">${escapeHtml(telemetryCostLabel(row.cost_source))}</span></td>
      <td>${formatNumber(row.user_prompts)}</td><td>${errorCell || "—"}</td>
    </tr>`;
              })
              .join("")
        : emptyTelemetryRow(16);
    $$(".telemetry-run-check", tbody).forEach((check) => {
        check.addEventListener("click", (event) =>
            event.stopPropagation(),
        );
        check.addEventListener("change", () => {
            const id = check.dataset.jobId;
            if (check.checked) TELEMETRY_STATE.selectedIds.add(id);
            else TELEMETRY_STATE.selectedIds.delete(id);
            TELEMETRY_STATE.selectionMode = TELEMETRY_STATE
                .selectedIds.size
                ? "selected"
                : "all";
            refreshTelemetry();
        });
    });
    $$("tr[data-telemetry-job]", tbody).forEach((row) => {
        row.addEventListener("click", (event) => {
            if (event.target.matches("input")) return;
            showTelemetryDetail(row.dataset.telemetryJob);
        });
    });
}
function renderTelemetryTimeseries(rows) {
    const wrap = $("#telemetry-timeseries");
    if (!wrap) return;
    if (!rows.length) {
        wrap.innerHTML =
            '<div class="empty">No timeseries data.</div>';
        return;
    }
    const max = Math.max(
        ...rows.map((row) => Number(row.submitted) || 0),
        1,
    );
    wrap.innerHTML = rows
        .map((row) => {
            const h =
                18 +
                Math.round(
                    ((Number(row.submitted) || 0) / max) * 52,
                );
            return `<div class="telemetry-bar" title="${escapeHtml(row.bucket)} · ${row.submitted} submitted" style="height:${h}px">${escapeHtml(row.submitted)}</div>`;
        })
        .join("");
}
function telemetryChartsAvailable() {
    return typeof echarts !== "undefined";
}
function telemetryEmptyChart(id, message) {
    const el = $(`#${id}`);
    if (!el) return;
    if (TELEMETRY_STATE.charts[id]) {
        TELEMETRY_STATE.charts[id].dispose();
        delete TELEMETRY_STATE.charts[id];
    }
    el.innerHTML = `<div class="empty">${escapeHtml(message || "No chart data.")}</div>`;
}
function telemetryChart(id) {
    const el = $(`#${id}`);
    if (!el || !telemetryChartsAvailable()) return null;
    if (!TELEMETRY_STATE.charts[id])
        TELEMETRY_STATE.charts[id] = echarts.init(el, null, {
            renderer: "canvas",
        });
    return TELEMETRY_STATE.charts[id];
}
function renderTelemetryChart(id, option, hasData) {
    if (!hasData) {
        telemetryEmptyChart(
            id,
            telemetryChartsAvailable()
                ? "No chart data."
                : "Chart library unavailable.",
        );
        return null;
    }
    const chart = telemetryChart(id);
    if (!chart) {
        telemetryEmptyChart(id, "Chart library unavailable.");
        return null;
    }
    chart.setOption(
        {
            backgroundColor: "transparent",
            color: [
                "#4a9dd4",
                "#c4873a",
                "#52c97b",
                "#d4a832",
                "#e05a46",
            ],
            textStyle: {
                color: "#8a8580",
                fontFamily: "JetBrains Mono, monospace",
                fontSize: 11,
            },
            grid: {
                left: 58,
                right: 28,
                top: 48,
                bottom: 50,
                containLabel: true,
            },
            tooltip: {
                trigger: "item",
                confine: true,
                backgroundColor: "#141720",
                borderColor: "rgba(255,255,255,.13)",
                textStyle: { color: "#e2ddd5" },
            },
            legend: {
                textStyle: {
                    color: "#8a8580",
                    fontFamily: "JetBrains Mono, monospace",
                    fontSize: 10,
                },
            },
            ...option,
        },
        true,
    );
    chart.resize();
    return chart;
}
function renderTelemetryCharts(charts) {
    const payload = charts || {};
    const durationPoints = payload.duration_points || [];
    const durationSeries = payload.duration_series || [];
    const durationChart = renderTelemetryChart(
        "telemetry-chart-duration",
        {
            legend: { top: 0, right: 8 },
            grid: {
                left: 62,
                right: 28,
                top: 54,
                bottom: 88,
                containLabel: true,
            },
            tooltip: {
                trigger: "item",
                confine: true,
                backgroundColor: "#141720",
                borderColor: "rgba(255,255,255,.13)",
                textStyle: { color: "#e2ddd5" },
            },
            xAxis: {
                type: "time",
                axisLabel: { hideOverlap: true },
            },
            yAxis: {
                type: "log",
                name: "Duration (s)",
                nameGap: 18,
                splitLine: {
                    lineStyle: { color: "rgba(255,255,255,.06)" },
                },
                min: 1,
            },
            dataZoom: [
                {
                    type: "inside",
                    filterMode: "none",
                    zoomOnMouseWheel: true,
                    moveOnMouseMove: true,
                    moveOnMouseWheel: true,
                },
                {
                    type: "slider",
                    filterMode: "none",
                    height: 30,
                    bottom: 28,
                    show: true,
                    showDetail: true,
                    showDataShadow: true,
                    realtime: true,
                    brushSelect: true,
                    borderColor: "rgba(74,157,212,.55)",
                    backgroundColor: "rgba(7,8,11,.76)",
                    fillerColor: "rgba(74,157,212,.22)",
                    handleSize: "120%",
                    handleStyle: {
                        color: "#e2ddd5",
                        borderColor: "#4a9dd4",
                        borderWidth: 2,
                        shadowBlur: 8,
                        shadowColor: "rgba(74,157,212,.25)",
                    },
                    moveHandleSize: 7,
                    moveHandleStyle: {
                        color: "#4a9dd4",
                        opacity: 0.85,
                    },
                    dataBackground: {
                        lineStyle: {
                            color: "rgba(255,255,255,.22)",
                        },
                        areaStyle: {
                            color: "rgba(255,255,255,.06)",
                        },
                    },
                    selectedDataBackground: {
                        lineStyle: { color: "#4a9dd4" },
                        areaStyle: {
                            color: "rgba(74,157,212,.18)",
                        },
                    },
                    textStyle: { color: "#c9c2b7" },
                },
            ],
            graphic: [
                {
                    type: "text",
                    left: "center",
                    bottom: 4,
                    silent: true,
                    style: {
                        text: "drag the time-window slider or handles to zoom",
                        fill: "#8a8580",
                        font: "10px JetBrains Mono, monospace",
                    },
                },
            ],
            series: [
                {
                    name: "Run",
                    type: "scatter",
                    symbolSize: (value) =>
                        Math.max(
                            6,
                            Math.min(
                                18,
                                Math.sqrt(
                                    Number(value?.[2] || 0) / 500,
                                ),
                            ),
                        ),
                    data: durationPoints
                        .filter(
                            (p) =>
                                p.submitted_at &&
                                p.complete_elapsed_seconds != null,
                        )
                        .map((p) => [
                            p.submitted_at,
                            p.complete_elapsed_seconds,
                            p.tokens_total || 0,
                            p.job_id,
                            p.status,
                        ]),
                    encode: { x: 0, y: 1 },
                    itemStyle: { color: "#4a9dd4" },
                },
                {
                    name: "Median",
                    type: "line",
                    showSymbol: true,
                    data: durationSeries
                        .filter(
                            (p) =>
                                p.bucket &&
                                p.complete_elapsed_median_seconds !=
                                    null,
                        )
                        .map((p) => [
                            p.bucket,
                            p.complete_elapsed_median_seconds,
                        ]),
                    lineStyle: { color: "#52c97b", width: 2 },
                },
                {
                    name: "P95",
                    type: "line",
                    showSymbol: false,
                    data: durationSeries
                        .filter(
                            (p) =>
                                p.bucket &&
                                p.complete_elapsed_p95_seconds !=
                                    null,
                        )
                        .map((p) => [
                            p.bucket,
                            p.complete_elapsed_p95_seconds,
                        ]),
                    lineStyle: {
                        color: "#d4a832",
                        type: "dashed",
                        width: 2,
                    },
                },
            ],
        },
        durationPoints.length || durationSeries.length,
    );
    durationChart?.off("click");
    durationChart?.on("click", (params) => {
        const jobId = params?.data?.[3];
        if (jobId) showTelemetryDetail(jobId);
    });

    const tokenSeries = payload.token_series || [];
    const positiveLogValue = (value) => {
        const n = Number(value);
        return n > 0 ? n : null;
    };
    renderTelemetryChart(
        "telemetry-chart-tokens",
        {
            legend: { top: 0, right: 8 },
            grid: {
                left: 62,
                right: 70,
                top: 54,
                bottom: 44,
                containLabel: true,
            },
            tooltip: {
                trigger: "axis",
                confine: true,
                backgroundColor: "#141720",
                borderColor: "rgba(255,255,255,.13)",
                textStyle: { color: "#e2ddd5" },
            },
            xAxis: {
                type: "category",
                data: tokenSeries.map((row) => row.bucket),
                axisLabel: { hideOverlap: true },
            },
            yAxis: [
                {
                    type: "log",
                    name: "Total tokens",
                    nameGap: 18,
                    min: 1,
                    splitLine: {
                        lineStyle: {
                            color: "rgba(255,255,255,.06)",
                        },
                    },
                },
                {
                    type: "log",
                    name: "Tokens/run",
                    nameGap: 18,
                    min: 1,
                    splitLine: { show: false },
                },
            ],
            series: [
                {
                    name: "Input",
                    type: "bar",
                    stack: "tokens",
                    barMaxWidth: 34,
                    data: tokenSeries.map((row) =>
                        positiveLogValue(row.tokens_in),
                    ),
                    itemStyle: { color: "#4a9dd4" },
                },
                {
                    name: "Output",
                    type: "bar",
                    stack: "tokens",
                    barMaxWidth: 34,
                    data: tokenSeries.map((row) =>
                        positiveLogValue(row.tokens_out),
                    ),
                    itemStyle: { color: "#c4873a" },
                },
                {
                    name: "Tokens/run",
                    type: "line",
                    yAxisIndex: 1,
                    showSymbol: true,
                    data: tokenSeries.map((row) =>
                        positiveLogValue(row.tokens_per_run),
                    ),
                    lineStyle: { color: "#52c97b", width: 2 },
                    itemStyle: { color: "#52c97b" },
                },
            ],
        },
        tokenSeries.some(
            (row) =>
                Number(row.tokens_in) > 0 ||
                Number(row.tokens_out) > 0 ||
                Number(row.tokens_per_run) > 0,
        ),
    );

    const stageBoxRows = payload.stage_duration_boxplot || [];
    const stageBoxEl = $("#telemetry-chart-stage-boxplot");
    if (stageBoxEl)
        stageBoxEl.style.height = stageBoxRows.length
            ? `${Math.max(372, stageBoxRows.length * 38 + 124)}px`
            : "";
    const stageBoxLabels = stageBoxRows.map(
        (row) => row.stage || "unattributed",
    );
    const stageBoxTooltip = (row) => {
        const safe = row || {};
        return (
            `<strong>${escapeHtml(safe.stage || "unattributed")}</strong><br>` +
            `Runs observed: ${formatNumber(safe.runs_observed || 0)}<br>` +
            `Min: ${formatSeconds(safe.min_seconds)} · P25: ${formatSeconds(safe.q1_seconds)}<br>` +
            `Median: ${formatSeconds(safe.median_seconds)} · P75: ${formatSeconds(safe.q3_seconds)}<br>` +
            `P95: ${formatSeconds(safe.p95_seconds)} · P99: ${formatSeconds(safe.p99_seconds)}<br>` +
            `Max: ${formatSeconds(safe.max_seconds)} · Failures: ${formatNumber(safe.failure_count || 0)} (${formatPercent(safe.failure_rate || 0)})`
        );
    };
    renderTelemetryChart(
        "telemetry-chart-stage-boxplot",
        {
            legend: {
                top: 0,
                right: 8,
                textStyle: { color: "#8a8580" },
            },
            grid: {
                left: 168,
                right: 44,
                top: 46,
                bottom: 72,
                containLabel: false,
            },
            tooltip: {
                trigger: "item",
                confine: true,
                backgroundColor: "#141720",
                borderColor: "rgba(255,255,255,.13)",
                textStyle: { color: "#e2ddd5" },
                formatter: (params) => {
                    if (params?.seriesType === "scatter")
                        return stageBoxTooltip(params?.data?.[2]);
                    return stageBoxTooltip(
                        params?.data?.row ||
                            stageBoxRows[params?.dataIndex],
                    );
                },
            },
            xAxis: {
                type: "value",
                name: "Duration (s)",
                nameGap: 34,
                nameLocation: "middle",
                nameTextStyle: {
                    color: "#e2ddd5",
                    fontWeight: 700,
                    padding: [12, 0, 0, 0],
                },
                splitLine: {
                    lineStyle: { color: "rgba(255,255,255,.06)" },
                },
            },
            yAxis: {
                type: "category",
                data: stageBoxLabels,
                inverse: true,
                axisLabel: {
                    interval: 0,
                    color: "#c9c2b7",
                    fontSize: 11,
                    margin: 14,
                },
            },
            series: [
                {
                    name: "Duration distribution",
                    type: "boxplot",
                    layout: "horizontal",
                    data: stageBoxRows.map((row) => ({
                        value: [
                            row.min_seconds,
                            row.q1_seconds,
                            row.median_seconds,
                            row.q3_seconds,
                            row.max_seconds,
                        ].map((value) => Number(value) || 0),
                        row,
                    })),
                    itemStyle: {
                        color: "rgba(74,157,212,.16)",
                        borderColor: "#4a9dd4",
                        borderWidth: 1.3,
                    },
                    emphasis: {
                        itemStyle: {
                            borderColor: "#e2ddd5",
                            borderWidth: 1.8,
                        },
                    },
                },
                {
                    name: "P95 marker",
                    type: "scatter",
                    data: stageBoxRows.map((row) => [
                        row.p95_seconds || 0,
                        row.stage || "unattributed",
                        row,
                    ]),
                    symbolSize: 8,
                    itemStyle: { color: "#d4a832" },
                    emphasis: { scale: 1.4 },
                },
            ],
        },
        stageBoxRows.length,
    );

    const stageTokenRows = payload.stage_token_boxplot || [];
    const stageTokenBoxEl = $(
        "#telemetry-chart-stage-token-boxplot",
    );
    if (stageTokenBoxEl)
        stageTokenBoxEl.style.height = stageTokenRows.length
            ? `${Math.max(372, stageTokenRows.length * 38 + 124)}px`
            : "";
    const stageTokenLabels = stageTokenRows.map(
        (row) => row.stage || "unattributed",
    );
    const stageTokenTooltip = (row) => {
        const safe = row || {};
        return (
            `<strong>${escapeHtml(safe.stage || "unattributed")}</strong><br>` +
            `Runs observed: ${formatNumber(safe.runs_observed || 0)}<br>` +
            `Min: ${formatNumber(safe.min_tokens)} · P25: ${formatNumber(safe.q1_tokens)}<br>` +
            `Median: ${formatNumber(safe.median_tokens)} · P75: ${formatNumber(safe.q3_tokens)}<br>` +
            `P95: ${formatNumber(safe.p95_tokens)} · P99: ${formatNumber(safe.p99_tokens)}<br>` +
            `Max: ${formatNumber(safe.max_tokens)} · Total: ${formatNumber(safe.total_tokens || 0)}`
        );
    };
    renderTelemetryChart(
        "telemetry-chart-stage-token-boxplot",
        {
            legend: {
                top: 0,
                right: 8,
                textStyle: { color: "#8a8580" },
            },
            grid: {
                left: 168,
                right: 44,
                top: 46,
                bottom: 72,
                containLabel: false,
            },
            tooltip: {
                trigger: "item",
                confine: true,
                backgroundColor: "#141720",
                borderColor: "rgba(255,255,255,.13)",
                textStyle: { color: "#e2ddd5" },
                formatter: (params) => {
                    if (params?.seriesType === "scatter")
                        return stageTokenTooltip(params?.data?.[2]);
                    return stageTokenTooltip(
                        params?.data?.row ||
                            stageTokenRows[params?.dataIndex],
                    );
                },
            },
            xAxis: {
                type: "log",
                name: "Tokens",
                nameGap: 34,
                nameLocation: "middle",
                nameTextStyle: {
                    color: "#e2ddd5",
                    fontWeight: 700,
                    padding: [12, 0, 0, 0],
                },
                min: 1,
                splitLine: {
                    lineStyle: { color: "rgba(255,255,255,.06)" },
                },
            },
            yAxis: {
                type: "category",
                data: stageTokenLabels,
                inverse: true,
                axisLabel: {
                    interval: 0,
                    color: "#c9c2b7",
                    fontSize: 11,
                    margin: 14,
                },
            },
            series: [
                {
                    name: "Token distribution",
                    type: "boxplot",
                    layout: "horizontal",
                    data: stageTokenRows.map((row) => ({
                        value: [
                            row.min_tokens,
                            row.q1_tokens,
                            row.median_tokens,
                            row.q3_tokens,
                            row.max_tokens,
                        ].map((value) => positiveLogValue(value)),
                        row,
                    })),
                    itemStyle: {
                        color: "rgba(82,201,123,.14)",
                        borderColor: "#52c97b",
                        borderWidth: 1.3,
                    },
                    emphasis: {
                        itemStyle: {
                            borderColor: "#e2ddd5",
                            borderWidth: 1.8,
                        },
                    },
                },
                {
                    name: "P95 marker",
                    type: "scatter",
                    data: stageTokenRows.map((row) => [
                        positiveLogValue(row.p95_tokens),
                        row.stage || "unattributed",
                        row,
                    ]),
                    symbolSize: 8,
                    itemStyle: { color: "#d4a832" },
                    emphasis: { scale: 1.4 },
                },
            ],
        },
        stageTokenRows.length,
    );

    const modelRows = (payload.model_points || [])
        .slice()
        .sort(
            (a, b) =>
                (Number(b.tokens_per_run) || 0) -
                    (Number(a.tokens_per_run) || 0) ||
                String(a.label || a.runner_model || "").localeCompare(
                    String(b.label || b.runner_model || ""),
                ),
        );
    const modelComparisonEl = $("#telemetry-chart-models");
    if (modelComparisonEl)
        modelComparisonEl.style.height = modelRows.length
            ? `${Math.max(346, modelRows.length * 34 + 104)}px`
            : "";
    renderTelemetryChart(
        "telemetry-chart-models",
        {
            grid: {
                left: 172,
                right: 78,
                top: 34,
                bottom: 64,
                containLabel: false,
            },
            tooltip: {
                trigger: "item",
                confine: true,
                backgroundColor: "#141720",
                borderColor: "rgba(255,255,255,.13)",
                textStyle: { color: "#e2ddd5" },
                formatter: (params) => {
                    const row = params?.data?.row || {};
                    return (
                        `<strong>${escapeHtml(row.label || row.runner_model || "unknown")}</strong><br>` +
                        `Model: ${escapeHtml(row.model_key || row.model || "unknown")}<br>` +
                        `Runs: ${formatNumber(row.runs || 0)}<br>` +
                        `Success: ${formatPct(row.success_rate || 0)} · Failure: ${formatPct(row.failure_rate || 0)}<br>` +
                        `Average runtime: ${formatDuration(row.average_runtime_seconds)} · P95 runtime: ${formatDuration(row.p95_runtime_seconds)}<br>` +
                        `Tokens/run: ${formatNumber(row.tokens_per_run || 0)} · Cost/run: ${formatMoney(row.cost_per_run || 0)}`
                    );
                },
            },
            xAxis: {
                type: "value",
                name: "Tokens/run",
                nameLocation: "middle",
                nameGap: 38,
                axisLabel: {
                    formatter: (value) =>
                        formatCompactNumber(value),
                },
                splitLine: {
                    lineStyle: { color: "rgba(255,255,255,.06)" },
                },
            },
            yAxis: {
                type: "category",
                data: modelRows.map(
                    (row) =>
                        row.label ||
                        row.runner_model ||
                        row.model_set ||
                        "unknown",
                ),
                inverse: true,
                axisLabel: {
                    interval: 0,
                    color: "#c9c2b7",
                    fontSize: 10,
                    margin: 12,
                },
            },
            series: [
                {
                    name: "Average tokens per run",
                    type: "bar",
                    barMaxWidth: 24,
                    data: modelRows.map((row) => ({
                        value: row.tokens_per_run || 0,
                        row,
                        itemStyle: {
                            color: telemetryColorForModel(
                                row.model_key || row.model,
                            ),
                        },
                    })),
                    label: {
                        show: true,
                        position: "right",
                        formatter: (params) =>
                            formatCompactNumber(params?.value),
                        color: "#c9c2b7",
                        fontSize: 10,
                    },
                },
            ],
        },
        modelRows.length,
    );

    const modelCountRows = (payload.model_run_counts || [])
        .slice()
        .sort(
            (a, b) =>
                (Number(b.average_runtime_seconds) || 0) -
                    (Number(a.average_runtime_seconds) || 0) ||
                String(a.model || "").localeCompare(
                    String(b.model || ""),
                ),
        );
    const modelCountEl = $("#telemetry-chart-model-counts");
    if (modelCountEl)
        modelCountEl.style.height = modelCountRows.length
            ? `${Math.max(346, modelCountRows.length * 34 + 104)}px`
            : "";
    renderTelemetryChart(
        "telemetry-chart-model-counts",
        {
            grid: {
                left: 146,
                right: 42,
                top: 34,
                bottom: 64,
                containLabel: false,
            },
            tooltip: {
                trigger: "item",
                confine: true,
                backgroundColor: "#141720",
                borderColor: "rgba(255,255,255,.13)",
                textStyle: { color: "#e2ddd5" },
                formatter: (params) => {
                    const row = params?.data?.row || {};
                    return (
                        `<strong>${escapeHtml(row.model || "unknown model")}</strong><br>` +
                        `Runs using model: ${formatNumber(row.runs || 0)}<br>` +
                        `Runners: ${escapeHtml((row.runners || []).join(", ") || "unknown")}<br>` +
                        `Success: ${formatPct(row.success_rate || 0)} · Failure: ${formatPct(row.failure_rate || 0)}<br>` +
                        `Average runtime: ${formatDuration(row.average_runtime_seconds)} · Tokens/run: ${formatNumber(row.tokens_per_run || 0)}`
                    );
                },
            },
            xAxis: {
                type: "value",
                name: "Average time/run",
                nameLocation: "middle",
                nameGap: 38,
                splitLine: {
                    lineStyle: { color: "rgba(255,255,255,.06)" },
                },
            },
            yAxis: {
                type: "category",
                data: modelCountRows.map(
                    (row) => row.model || "unknown model",
                ),
                inverse: true,
                axisLabel: {
                    interval: 0,
                    color: "#c9c2b7",
                    fontSize: 10,
                    margin: 12,
                },
            },
            series: [
                {
                    name: "Average time per run",
                    type: "bar",
                    barMaxWidth: 24,
                    data: modelCountRows.map((row) => ({
                        value: row.average_runtime_seconds || 0,
                        row,
                        itemStyle: {
                            color: telemetryColorForModel(
                                row.model_key || row.model,
                            ),
                        },
                    })),
                    label: {
                        show: true,
                        position: "right",
                        formatter: (params) =>
                            formatDuration(params?.value),
                        color: "#c9c2b7",
                        fontSize: 10,
                    },
                },
            ],
        },
        modelCountRows.length,
    );

    const acRows = (payload.ac_complexity_points || []).filter(
        (row) =>
            row.original_ac_count != null &&
            row.complete_elapsed_seconds != null,
    );
    renderTelemetryChart(
        "telemetry-chart-ac",
        {
            grid: {
                left: 54,
                right: 26,
                top: 34,
                bottom: 42,
                containLabel: true,
            },
            xAxis: {
                type: "value",
                name: "Original ACs",
                splitLine: {
                    lineStyle: { color: "rgba(255,255,255,.06)" },
                },
            },
            yAxis: {
                type: "value",
                name: "Duration (s)",
                splitLine: {
                    lineStyle: { color: "rgba(255,255,255,.06)" },
                },
            },
            series: [
                {
                    type: "scatter",
                    symbolSize: (value) =>
                        Math.max(
                            6,
                            Math.min(
                                22,
                                Math.sqrt(
                                    Number(value?.[2] || 0) / 500,
                                ),
                            ),
                        ),
                    data: acRows.map((row) => [
                        row.original_ac_count,
                        row.complete_elapsed_seconds,
                        row.tokens_total || 0,
                        row.job_id,
                    ]),
                    itemStyle: { color: "#d4a832" },
                },
            ],
        },
        acRows.length,
    );

    const loopRows = payload.loop_iteration_series || [];
    const loopBuckets = [
        ...new Set(loopRows.map((row) => row.bucket)),
    ];
    const loopNames = [
        ...new Set(
            loopRows.map((row) => row.loop_name || "unknown"),
        ),
    ];
    renderTelemetryChart(
        "telemetry-chart-loops",
        {
            legend: {
                type: "scroll",
                top: 0,
                right: 8,
                left: 8,
                textStyle: { color: "#8a8580" },
            },
            grid: {
                left: 58,
                right: 26,
                top: 62,
                bottom: 48,
                containLabel: true,
            },
            tooltip: {
                trigger: "axis",
                axisPointer: { type: "shadow" },
                confine: true,
                backgroundColor: "#141720",
                borderColor: "rgba(255,255,255,.13)",
                textStyle: { color: "#e2ddd5" },
            },
            xAxis: {
                type: "category",
                data: loopBuckets,
                axisLabel: { hideOverlap: true },
            },
            yAxis: {
                type: "value",
                name: "Total iterations",
                splitLine: {
                    lineStyle: { color: "rgba(255,255,255,.06)" },
                },
            },
            series: loopNames.map((name, index) => ({
                name,
                type: "bar",
                stack: "loop-total",
                barMaxWidth: 38,
                emphasis: { focus: "series" },
                data: loopBuckets.map((bucket) => {
                    const row =
                        loopRows.find(
                            (item) =>
                                item.bucket === bucket &&
                                (item.loop_name || "unknown") ===
                                    name,
                        ) || {};
                    return {
                        value: row.total_iterations || 0,
                        name: bucket,
                        median_iterations: row.median_iterations,
                        p95_iterations: row.p95_iterations,
                        run_count: row.run_count,
                    };
                }),
            })),
        },
        loopRows.length,
    );
}
function renderTelemetryProfileCharts(profile) {
    const stageSpans = profile?.stage_spans || [];
    renderTelemetryChart(
        "telemetry-detail-waterfall",
        {
            grid: { left: 110, right: 18, top: 18, bottom: 30 },
            xAxis: { type: "value", name: "seconds" },
            yAxis: {
                type: "category",
                data: stageSpans.map(
                    (span, index) =>
                        `${index + 1}. ${span.stage || "stage"}`,
                ),
            },
            series: [
                {
                    type: "bar",
                    data: stageSpans.map(
                        (span) => span.duration_seconds || 0,
                    ),
                    itemStyle: { color: "#c4873a" },
                },
            ],
        },
        stageSpans.length,
    );
    const tokenStages = profile?.token_by_stage || [];
    renderTelemetryChart(
        "telemetry-detail-token-stage",
        {
            legend: { top: 0, textStyle: { color: "#8a8580" } },
            xAxis: { type: "value", name: "tokens" },
            yAxis: {
                type: "category",
                data: tokenStages.map(
                    (row) => row.stage || "unattributed",
                ),
            },
            series: [
                {
                    name: "Input",
                    type: "bar",
                    stack: "tokens",
                    data: tokenStages.map(
                        (row) => row.tokens_in || 0,
                    ),
                    itemStyle: { color: "#8fb4ff" },
                },
                {
                    name: "Output",
                    type: "bar",
                    stack: "tokens",
                    data: tokenStages.map(
                        (row) => row.tokens_out || 0,
                    ),
                    itemStyle: { color: "#c4873a" },
                },
            ],
        },
        tokenStages.length,
    );
    const llmCalls = profile?.llm_calls || [];
    const start = Date.parse(
        profile?.started_at || profile?.submitted_at || "",
    );
    renderTelemetryChart(
        "telemetry-detail-llm-timeline",
        {
            xAxis: { type: "value", name: "seconds from start" },
            yAxis: { type: "value", name: "tokens" },
            series: [
                {
                    type: "scatter",
                    symbolSize: (value) =>
                        Math.max(
                            6,
                            Math.min(
                                22,
                                Math.sqrt(
                                    Number(value?.[1] || 0) / 250,
                                ),
                            ),
                        ),
                    data: llmCalls.map((call) => [
                        Math.max(
                            0,
                            ((Date.parse(call.ts || "") || start) -
                                start) /
                                1000,
                        ),
                        call.tokens_total || 0,
                        call.model,
                        call.stage,
                    ]),
                    itemStyle: { color: "#6ee7a0" },
                },
            ],
        },
        llmCalls.length && Number.isFinite(start),
    );
}
async function showTelemetryDetail(jobId) {
    TELEMETRY_STATE.detailJobId = jobId;
    const detail = $("#telemetry-detail");
    detail.classList.add("show");
    detail.dataset.jobId = jobId;
    detail.innerHTML =
        '<div class="empty">Loading run detail...</div>';
    try {
        const [job, events, profilePayload] = await Promise.all([
            api(`/runs/${jobId}`),
            api(`/runs/${jobId}/events`),
            api(`/telemetry/runs/${jobId}/profile`),
        ]);
        const profile = profilePayload?.profile || {};
        const row =
            (TELEMETRY_STATE.aggregate?.run_rows || []).find(
                (r) => r.id === jobId,
            ) || {};
        const stages = buildWorkflowStages(job, events || []);
        const opik = job.opik?.dashboard_url
            ? `<a class="btn link-btn" href="${escapeHtml(job.opik.dashboard_url)}" target="_blank" rel="noreferrer">Open Opik link</a>`
            : "";
        detail.innerHTML = `
      <div class="telemetry-panel-head" style="padding:0 0 12px;border-bottom:none">
        <div><div class="telemetry-panel-title">${escapeHtml(job.change_id || job.id)}</div><div class="telemetry-panel-meta">${escapeHtml(job.id)} · ${escapeHtml(telemetryCoverageLabel(row.coverage_source || (events.length ? "sqlite" : "none")))}</div></div>
        <div class="telemetry-actions" style="padding:0"><button class="btn ghost" id="telemetry-open-run" type="button">Open in Runs tab</button>${opik}</div>
      </div>
      <div class="telemetry-grid">
        <div><div class="section-label">Stage waterfall</div>${stages.map((stage) => `<div class="job-meta">${escapeHtml(stage.label)} · ${escapeHtml(stage.state)} · ${escapeHtml(stage.timerText)}</div>`).join("")}</div>
        <div><div class="section-label">Token/cost summary</div><div class="job-meta">tokens ${formatNumber(profile.tokens?.tokens_total ?? (job.tokens_in || 0) + (job.tokens_out || 0))} · cost ${formatMoney(profile.tokens?.estimated_cost_usd ?? (row.estimated_cost_usd || row.cost_usd || 0))} · ${escapeHtml(profile.tokens?.source || telemetryCostLabel(row.cost_source))}</div><div class="section-label" style="margin-top:12px">Models used</div><div class="job-meta">${
(profile.models_used || [row.model || job.model])
    .filter(Boolean)
    .map(
        (model) =>
            `<span class="telemetry-badge">${escapeHtml(model)}</span>`,
    )
    .join("") || "—"
        }</div><div class="section-label" style="margin-top:12px">ACs / loops</div><div class="job-meta">Original ACs ${escapeHtml(profile.original_ac_count ?? "—")} · loops ${formatNumber((profile.loop_instances || []).length)}</div><div class="section-label" style="margin-top:12px">Error summary</div><div class="job-meta">${escapeHtml(row.error_summary || job.error_message || "No error recorded.")}</div></div>
      </div>
      <div class="telemetry-grid" style="margin-top:14px">
        <div class="telemetry-chart-card"><div class="telemetry-chart-title">Run waterfall</div><div id="telemetry-detail-waterfall" class="telemetry-chart"></div></div>
        <div class="telemetry-chart-card"><div class="telemetry-chart-title">Tokens by stage</div><div id="telemetry-detail-token-stage" class="telemetry-chart"></div></div>
      </div>
      <div class="telemetry-chart-card" style="margin-top:14px"><div class="telemetry-chart-title">LLM call timeline</div><div id="telemetry-detail-llm-timeline" class="telemetry-chart"></div></div>
      <div class="section-label" style="margin-top:14px">Loop instances</div>
      <div class="telemetry-table-wrap"><table class="telemetry-table"><thead><tr><th>Loop</th><th>Stage</th><th>UOW</th><th>Iterations</th><th>Max</th><th>Result</th><th>Source</th></tr></thead><tbody>${(profile.loop_instances || []).length ? (profile.loop_instances || []).map((loop) => `<tr><td>${escapeHtml(loop.loop_name || "")}</td><td>${escapeHtml(loop.stage || "—")}</td><td>${escapeHtml(loop.uow_id || "—")}</td><td>${formatNumber(loop.actual_iterations)}</td><td>${escapeHtml(loop.max_iterations ?? "—")}</td><td>${escapeHtml(loop.passed == null ? "—" : loop.passed ? "passed" : "failed")}</td><td>${escapeHtml(loop.source || "")}</td></tr>`).join("") : emptyTelemetryRow(7)}</tbody></table></div>
      <div class="section-label" style="margin-top:14px">User prompts</div>
      <div class="job-meta">${formatNumber((events || []).filter((ev) => ev.type === "user.prompt").length)} prompt events</div>
      <div class="section-label" style="margin-top:14px">Raw events</div>
      <pre>${escapeHtml(JSON.stringify(events || [], null, 2))}</pre>`;
        renderTelemetryProfileCharts(profile);
        $("#telemetry-open-run")?.addEventListener("click", () => {
            document
                .querySelector('.nav-item[data-view="runs"]')
                ?.click();
            selectJob(jobId);
        });
    } catch (e) {
        detail.innerHTML = `<div class="empty error">${escapeHtml(e.message)}</div>`;
    }
}
async function refreshTelemetry() {
    if (TELEMETRY_STATE.loading) return;
    TELEMETRY_STATE.loading = true;
    try {
        const filters = telemetryFilters();
        const runs = await api(telemetryRunsQuery(filters));
        TELEMETRY_STATE.visibleRuns = runs.items || [];
        populateTelemetryOptions(runs.filter_options);
        if (
            !TELEMETRY_STATE.autoSelected &&
            TELEMETRY_STATE.selectionMode === "all" &&
            TELEMETRY_STATE.selectedIds.size === 0
        ) {
            let defaultRun = pickDefaultTelemetryRun(
                TELEMETRY_STATE.visibleRuns,
            );
            if (!defaultRun) {
                const current = await api(
                    "/telemetry/runs?limit=1&status=running",
                );
                defaultRun = pickDefaultTelemetryRun(
                    current.items || [],
                );
            }
            if (!defaultRun) {
                const latest = await api("/telemetry/runs?limit=1");
                defaultRun = pickDefaultTelemetryRun(
                    latest.items || [],
                );
            }
            if (defaultRun?.id) {
                TELEMETRY_STATE.selectedIds = new Set([
                    defaultRun.id,
                ]);
                TELEMETRY_STATE.selectionMode = "selected";
                TELEMETRY_STATE.detailJobId = defaultRun.id;
                TELEMETRY_STATE.autoSelected = true;
            }
        }
        const payload = {
            selection_mode: TELEMETRY_STATE.selectionMode,
            job_ids:
                TELEMETRY_STATE.selectionMode === "selected"
                    ? [...TELEMETRY_STATE.selectedIds]
                    : [],
            filters,
            bucket: TELEMETRY_STATE.chartBucket || "day",
            rollup: TELEMETRY_STATE.rollup || "run",
        };
        const data = await api("/telemetry/query", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(payload),
        });
        TELEMETRY_STATE.lastRefresh = new Date();
        TELEMETRY_STATE.loaded = true;
        const content = $(".telemetry-content");
        const atBottom =
            content &&
            content.scrollHeight -
                content.scrollTop -
                content.clientHeight <
                100;
        const savedScrollTop = content ? content.scrollTop : 0;
        renderTelemetryAggregate(data);
        const detailAlreadyShown =
            TELEMETRY_STATE.detailJobId &&
            $("#telemetry-detail")?.classList.contains("show") &&
            $("#telemetry-detail")?.dataset.jobId ===
                TELEMETRY_STATE.detailJobId;
        if (
            TELEMETRY_STATE.detailJobId &&
            !detailAlreadyShown &&
            (data.run_rows || []).some(
                (row) => row.id === TELEMETRY_STATE.detailJobId,
            )
        ) {
            showTelemetryDetail(TELEMETRY_STATE.detailJobId);
        }
        if (content && savedScrollTop > 0) {
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    if (atBottom) {
                        content.scrollTop = content.scrollHeight;
                    } else {
                        content.scrollTop = Math.min(
                            savedScrollTop,
                            content.scrollHeight -
                                content.clientHeight,
                        );
                    }
                });
            });
        }
    } catch (e) {
        $("#telemetry-meta").textContent = "Telemetry failed";
        $("#telemetry-cards").innerHTML =
            `<div class="empty error">${escapeHtml(e.message)}</div>`;
    } finally {
        TELEMETRY_STATE.loading = false;
    }
}
function scheduleTelemetryRefresh() {
    clearTimeout(TELEMETRY_STATE.debounce);
    TELEMETRY_STATE.debounce = setTimeout(refreshTelemetry, 150);
}
function syncTelemetryPolling() {
    if (telemetryActive()) {
        if (!TELEMETRY_STATE.timer)
            TELEMETRY_STATE.timer = setInterval(
                refreshTelemetry,
                5000,
            );
        if (!TELEMETRY_STATE.loaded) refreshTelemetry();
    } else if (TELEMETRY_STATE.timer) {
        clearInterval(TELEMETRY_STATE.timer);
        TELEMETRY_STATE.timer = null;
    }
}
function resetTelemetryFilters() {
    $("#telemetry-range").value = "all";
    $("#telemetry-start").value = "";
    $("#telemetry-end").value = "";
    $("#telemetry-status").selectedIndex = -1;
    for (let o of $("#telemetry-status").options)
        o.selected = false;
    $("#telemetry-runner").value = "";
    $("#telemetry-model").value = "";
    $("#telemetry-run-kind").value = "";
    $("#telemetry-mode").value = "";
    $("#telemetry-repo").value = "";
    $("#telemetry-change").value = "";
    $("#telemetry-q").value = "";
    $("#telemetry-sort").value = "newest";
    TELEMETRY_STATE.selectedIds.clear();
    TELEMETRY_STATE.selectionMode = "all";
    TELEMETRY_STATE.failedStage = null;
    TELEMETRY_STATE.missingEventsOnly = false;
    scheduleTelemetryRefresh();
}
function clearTelemetryAfterDelete() {
    TELEMETRY_STATE.loaded = false;
    TELEMETRY_STATE.loading = false;
    TELEMETRY_STATE.selectedIds.clear();
    TELEMETRY_STATE.visibleRuns = [];
    TELEMETRY_STATE.selectionMode = "all";
    TELEMETRY_STATE.aggregate = null;
    TELEMETRY_STATE.detailJobId = null;
    TELEMETRY_STATE.failedStage = null;
    TELEMETRY_STATE.missingEventsOnly = false;
    const detail = $("#telemetry-detail");
    if (detail) {
        detail.classList.remove("show");
        detail.dataset.jobId = "";
        detail.innerHTML = "";
    }
}
function setTelemetryDeleteMessage(message, isError = false, kind = "") {
    toast(message, isError, kind);
    const meta = $("#telemetry-meta");
    if (meta) meta.textContent = message;
}
async function deleteAllTelemetryData() {
    setTelemetryDeleteMessage("Delete all data clicked.");
    if (
        !confirm(
            "Delete all run telemetry data? This cannot be undone.",
        )
    ) {
        setTelemetryDeleteMessage("Delete all data canceled.");
        return;
    }
    const button = $("#telemetry-delete-all");
    if (button) button.disabled = true;
    setTelemetryDeleteMessage("Deleting telemetry data...");
    try {
        await api("/telemetry/data", { method: "DELETE" });
        clearTelemetryAfterDelete();
        await refreshTelemetry();
        setTelemetryDeleteMessage(
            "Telemetry data deleted.",
            false,
            "success",
        );
    } catch (e) {
        toast(e.message, true);
        const meta = $("#telemetry-meta");
        if (meta) meta.textContent = e.message;
    } finally {
        if (button) button.disabled = false;
    }
}
function applyTelemetryPreset(preset) {
    TELEMETRY_STATE.failedStage = null;
    TELEMETRY_STATE.missingEventsOnly = false;
    if (preset === "active") {
        $("#telemetry-status").value = "running";
    }
    if (preset === "attention" || preset === "awaiting") {
        $("#telemetry-status").value = "awaiting_input";
    }
    if (preset === "failed" || preset === "expensive-failures") {
        $("#telemetry-status").value = "failed";
    }
    if (preset === "tokens") {
        TELEMETRY_STATE.selectedIds.clear();
        TELEMETRY_STATE.selectionMode = "all";
    }
    if (preset === "missing") {
        TELEMETRY_STATE.selectedIds.clear();
        TELEMETRY_STATE.selectionMode = "all";
        TELEMETRY_STATE.missingEventsOnly = true;
    }
    if (preset === "slow") $("#telemetry-sort").value = "slowest";
    if (preset === "tokens") $("#telemetry-sort").value = "tokens";
    if (preset === "expensive-failures")
        $("#telemetry-sort").value = "expensive";
    scheduleTelemetryRefresh();
}
function initTelemetry() {
    [
        "#telemetry-range",
        "#telemetry-start",
        "#telemetry-end",
        "#telemetry-status",
        "#telemetry-runner",
        "#telemetry-model",
        "#telemetry-run-kind",
        "#telemetry-mode",
        "#telemetry-repo",
        "#telemetry-change",
        "#telemetry-q",
        "#telemetry-sort",
        "#telemetry-chart-bucket",
        "#telemetry-rollup",
    ].forEach((selector) => {
        const el = $(selector);
        if (!el) return;
        el.addEventListener(
            el.tagName === "INPUT" ? "input" : "change",
            () => {
                if (
                    selector !== "#telemetry-chart-bucket" &&
                    selector !== "#telemetry-rollup"
                )
                    TELEMETRY_STATE.missingEventsOnly = false;
                TELEMETRY_STATE.chartBucket =
                    $("#telemetry-chart-bucket")?.value || "day";
                TELEMETRY_STATE.rollup =
                    $("#telemetry-rollup")?.value || "run";
                scheduleTelemetryRefresh();
            },
        );
    });
    $("#telemetry-refresh")?.addEventListener(
        "click",
        refreshTelemetry,
    );
    $("#telemetry-reset-filters")?.addEventListener(
        "click",
        resetTelemetryFilters,
    );
    const deleteButton = $("#telemetry-delete-all");
    if (deleteButton) {
        deleteButton.dataset.telemetryDeleteBound = "main";
        deleteButton.addEventListener("click", deleteAllTelemetryData);
    }
    $("#telemetry-all")?.addEventListener("click", () => {
        TELEMETRY_STATE.selectedIds.clear();
        TELEMETRY_STATE.selectionMode = "all";
        refreshTelemetry();
    });
    $("#telemetry-select-visible")?.addEventListener(
        "click",
        () => {
            TELEMETRY_STATE.visibleRuns.forEach((row) =>
                TELEMETRY_STATE.selectedIds.add(row.id),
            );
            TELEMETRY_STATE.selectionMode = TELEMETRY_STATE
                .selectedIds.size
                ? "selected"
                : "all";
            refreshTelemetry();
        },
    );
    $("#telemetry-clear")?.addEventListener("click", () => {
        TELEMETRY_STATE.selectedIds.clear();
        TELEMETRY_STATE.selectionMode = "all";
        refreshTelemetry();
    });
    $$("#telemetry-presets .telemetry-chip").forEach((btn) =>
        btn.addEventListener("click", () =>
            applyTelemetryPreset(btn.dataset.preset),
        ),
    );
}
initTelemetry();
window.addEventListener("resize", () => {
    Object.values(TELEMETRY_STATE.charts || {}).forEach((chart) =>
        chart.resize(),
    );
});
