/*
 * Runs view.
 *
 * Owns history filtering, run submission, live/replayed event rendering,
 * terminal formatting, Opik trace summaries, and SSE stream lifecycle.
 */

function fmtLocalTime(isoStr) {
    if (!isoStr) return "";
    const d = new Date(isoStr);
    if (Number.isNaN(d.getTime())) return isoStr;
    return d.toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
        timeZoneName: "short",
    });
}
function fmtLocalClockTime(isoStr) {
    if (!isoStr) return "";
    const d = new Date(isoStr);
    if (Number.isNaN(d.getTime())) return isoStr;
    return d.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
        timeZoneName: "short",
    });
}

// History
const STATUS_ICON = {
    failed: "✕ ",
    succeeded: "✓ ",
    cancelled: "⊘ ",
    running: "● ",
    queued: "◌ ",
    awaiting_input: "? ",
};
function isHistoryRangeMatch(job, range) {
    if (!range || range === "all") return true;
    const submitted = Date.parse(
        job.submitted_at || job.updated_at || "",
    );
    if (!Number.isFinite(submitted)) return false;
    const ageMs = Date.now() - submitted;
    if (range === "today") {
        const d = new Date(submitted);
        const now = new Date();
        return (
            d.getFullYear() === now.getFullYear() &&
            d.getMonth() === now.getMonth() &&
            d.getDate() === now.getDate()
        );
    }
    if (range === "24h") return ageMs <= 24 * 60 * 60 * 1000;
    if (range === "7d") return ageMs <= 7 * 24 * 60 * 60 * 1000;
    if (range === "30d") return ageMs <= 30 * 24 * 60 * 60 * 1000;
    return true;
}
function isHistoryStatusMatch(job, status) {
    if (!status || status === "all") return true;
    if (status === "active")
        return RUN_ACTIVE_STATUSES.has(job.status);
    return job.status === status;
}
function filteredHistoryItems() {
    return (RUN_HISTORY_STATE.items || []).filter(
        (job) =>
            isHistoryStatusMatch(job, RUN_HISTORY_STATE.status) &&
            isHistoryRangeMatch(job, RUN_HISTORY_STATE.range) &&
            (RUN_HISTORY_STATE.runner === "all" ||
                job.runner === RUN_HISTORY_STATE.runner),
    );
}
function updateHistoryFilterControls() {
    $$("[data-history-status]").forEach((btn) =>
        btn.classList.toggle(
            "active",
            btn.dataset.historyStatus === RUN_HISTORY_STATE.status,
        ),
    );
    $$("[data-history-range]").forEach((btn) =>
        btn.classList.toggle(
            "active",
            btn.dataset.historyRange === RUN_HISTORY_STATE.range,
        ),
    );
    const runnerSelect = $("#history-runner-filter");
    if (runnerSelect) runnerSelect.value = RUN_HISTORY_STATE.runner;
}
function syncHistoryDisclosure() {
    const section = $("#runs-history-section");
    const panel = $("#history-panel");
    const button = $("#history-toggle");
    const label = $("#history-toggle-label");
    if (!section || !panel || !button) return;
    const expanded = !RUN_HISTORY_STATE.collapsed;
    section.classList.toggle("is-collapsed", !expanded);
    panel.hidden = !expanded;
    button.setAttribute(
        "aria-expanded",
        expanded ? "true" : "false",
    );
    button.setAttribute(
        "aria-label",
        expanded ? "Collapse Past Runs" : "Expand Past Runs",
    );
    button.title = expanded
        ? "Collapse Past Runs"
        : "Expand Past Runs";
    if (label) label.textContent = expanded ? "Collapse" : "Expand";
}
function refreshHistoryRunnerOptions(items) {
    const select = $("#history-runner-filter");
    if (!select) return;
    const current = RUN_HISTORY_STATE.runner || "all";
    const runners = [
        ...new Set(
            (items || []).map((job) => job.runner).filter(Boolean),
        ),
    ].sort((a, b) => a.localeCompare(b));
    select.innerHTML = '<option value="all">Any runner</option>';
    runners.forEach((runner) => {
        const option = document.createElement("option");
        option.value = runner;
        option.textContent = runner;
        select.appendChild(option);
    });
    if (runners.includes(current)) select.value = current;
    else {
        RUN_HISTORY_STATE.runner = "all";
        select.value = "all";
    }
}
function renderHistory() {
    const wrap = $("#history");
    if (!wrap) return;
    const allItems = RUN_HISTORY_STATE.items || [];
    refreshHistoryRunnerOptions(allItems);
    updateHistoryFilterControls();
    const items = filteredHistoryItems();
    wrap.innerHTML = "";
    if (!allItems.length) {
        const opt = document.createElement("option");
        opt.textContent = "No runs yet.";
        opt.disabled = true;
        wrap.appendChild(opt);
    } else if (!items.length) {
        const opt = document.createElement("option");
        opt.textContent = "No runs match the current filters.";
        opt.disabled = true;
        wrap.appendChild(opt);
    }
    items.forEach((j) => {
        const opt = document.createElement("option");
        opt.value = j.id;
        const status = j.status || "unknown";
        const icon = STATUS_ICON[status] || "";
        const changeId = j.change_id || "no change id";
        const runnerModel =
            [j.runner, j.model].filter(Boolean).join("/") ||
            "runner?";
        const time = fmtLocalTime(j.submitted_at);
        opt.textContent = `${icon} ${(j.id || "").slice(0, 16)}...  ${changeId}  ${runnerModel}  ${time}`;
        if (j.id === activeJobId) opt.selected = true;
        wrap.appendChild(opt);
    });
    if (activeJobId && items.some((job) => job.id === activeJobId)) {
        wrap.value = activeJobId;
    } else {
        wrap.selectedIndex = -1;
    }
    const total = allItems.length;
    const count = items.length;
    const countText =
        count === total ? `${total}` : `${count}/${total}`;
    const countEl = $("#history-count");
    if (countEl) countEl.textContent = countText;
    const summary = $("#history-filter-summary");
    if (summary) {
        const parts = [];
        if (RUN_HISTORY_STATE.status !== "all")
            parts.push(`status=${RUN_HISTORY_STATE.status}`);
        if (RUN_HISTORY_STATE.range !== "all")
            parts.push(`date=${RUN_HISTORY_STATE.range}`);
        if (RUN_HISTORY_STATE.runner !== "all")
            parts.push(`runner=${RUN_HISTORY_STATE.runner}`);
        summary.textContent = parts.length
            ? `Showing ${count} of ${total} loaded runs (${parts.join(" · ")})`
            : `Showing ${total} most recent runs`;
    }
    $("#runs-meta").textContent =
        `${countText} run${count === 1 ? "" : "s"}`;
}
async function loadHistory() {
    try {
        const r = await api("/runs?limit=500");
        RUN_HISTORY_STATE.items = r.items || [];
        renderHistory();
        if (activeJobId && !activeStream && activeRunJob) {
            const fresh = RUN_HISTORY_STATE.items.find(
                (j) => j.id === activeJobId,
            );
            if (
                fresh &&
                (fresh.status !== activeRunJob.status ||
                    fresh.current_stage !==
                        activeRunJob.current_stage ||
                    Number(fresh.tokens_in || 0) !==
                        Number(activeRunJob.tokens_in || 0) ||
                    Number(fresh.tokens_out || 0) !==
                        Number(activeRunJob.tokens_out || 0) ||
                    Number(fresh.cost_usd || 0) !==
                        Number(activeRunJob.cost_usd || 0))
            ) {
                setActiveSurfaceJob("runs", {
                    ...activeRunJob,
                    status: fresh.status,
                    current_stage: fresh.current_stage,
                    tokens_in: fresh.tokens_in,
                    tokens_out: fresh.tokens_out,
                    cost_usd: fresh.cost_usd,
                });
                renderRunWorkflow(activeRunJob, activeRunEvents);
                updateRunFeedback(activeRunJob, activeRunEvents);
                renderSurfaceMetrics("runs");
            }
        }
    } catch (e) {
        /* server may be starting */
    }
}
$$("[data-history-status]").forEach((btn) =>
    btn.addEventListener("click", () => {
        RUN_HISTORY_STATE.status =
            btn.dataset.historyStatus || "all";
        renderHistory();
    }),
);
$$("[data-history-range]").forEach((btn) =>
    btn.addEventListener("click", () => {
        RUN_HISTORY_STATE.range = btn.dataset.historyRange || "all";
        renderHistory();
    }),
);
$("#history-runner-filter")?.addEventListener("change", (event) => {
    RUN_HISTORY_STATE.runner = event.target.value || "all";
    renderHistory();
});
let lastHistorySelection = { id: "", at: 0 };
function handleHistorySelection(event) {
    const id = event.currentTarget?.value || "";
    if (!id) return;
    const now = Date.now();
    if (
        id === lastHistorySelection.id &&
        now - lastHistorySelection.at < 250
    ) {
        return;
    }
    lastHistorySelection = { id, at: now };
    selectJob(id);
}
const historyList = $("#history");
historyList?.addEventListener("input", handleHistorySelection);
historyList?.addEventListener("change", handleHistorySelection);
historyList?.addEventListener("click", handleHistorySelection);
$("#history-toggle")?.addEventListener("click", () => {
    RUN_HISTORY_STATE.collapsed = !RUN_HISTORY_STATE.collapsed;
    syncHistoryDisclosure();
});
syncHistoryDisclosure();
setInterval(loadHistory, 3000);
loadHistory();

function setSubmitFeedback(kind = "", message = "") {
    const el = $("#submit-feedback");
    if (!el) return;
    el.textContent = message || "";
    el.className =
        "submit-feedback" +
        (message ? ` show ${kind || "info"}` : "");
}
function setSubmitLoading(loading) {
    const btn = $("#submit-btn");
    if (!btn) return;
    btn.disabled = !!loading;
    btn.classList.toggle("is-loading", !!loading);
    const label = btn.querySelector(".submit-btn-text");
    if (label)
        label.textContent = loading
            ? "Submitting..."
            : "Submit run";
}
function failSubmitValidation(message) {
    setSubmitFeedback("error", message);
    toast(message, true);
}

// Submit
$("#submit-btn").addEventListener("click", async () => {
    if (submitRunInFlight) return;
    setSubmitFeedback();
    const source = ACTIVE_STORY_SOURCE || "manual";
    const extraContext = $("#f-extra").value.trim();
    const body = {
        repo: $("#f-repo").value.trim(),
        change_id: $("#f-change").value.trim() || null,
        runner: $("#f-runner").value,
        model: $("#f-model").value || null,
        log_level: $("#f-log-level").value,
        extra_context: extraContext || null,
        mode: $("#f-mode").value,
    };
    const agentOverrides = collectRunAgentOverrides();
    if (Object.keys(agentOverrides).length) {
        body.agent_llm_overrides = agentOverrides;
    }
    if (!body.repo) {
        failSubmitValidation("Repo path is required.");
        return;
    }
    if (source === "manual") {
        const title = $("#f-manual-title").value.trim();
        const description = $("#f-manual-description").value.trim();
        const acceptanceCriteria = $("#f-manual-ac").value.trim();
        if (!title || !description || !acceptanceCriteria) {
            failSubmitValidation(
                "Manual story title, description, and acceptance criteria are required.",
            );
            return;
        }
        body.manual_story = {
            work_item_id:
                $("#f-manual-work-item-id").value.trim() || null,
            work_item_url:
                $("#f-manual-work-item-url").value.trim() || null,
            title,
            description,
            acceptance_criteria: acceptanceCriteria,
            extra_context: extraContext || null,
        };
    } else if (source === "ado") {
        body.ado_url = $("#f-ado").value.trim() || null;
        if (!body.ado_url) {
            failSubmitValidation(
                "ADO Work Item URL is required for Azure DevOps runs.",
            );
            return;
        }
    } else {
        body.story_file = $("#f-story-file").value.trim() || null;
        if (!body.story_file) {
            failSubmitValidation(
                "Story fixture path is required for local fixture runs.",
            );
            return;
        }
    }
    submitRunInFlight = true;
    setSubmitLoading(true);
    setSubmitFeedback(
        "info",
        "Submitting run and opening the live log panel...",
    );
    try {
        const r = await api("/runs", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(body),
        });
        setSubmitFeedback(
            "success",
            `Submitted ${r.job_id}. Opening live logs now.`,
        );
        try {
            await rememberRepoPath(body.repo);
            toast("Submitted " + r.job_id, false, "success");
        } catch (saveErr) {
            toast(
                `Submitted ${r.job_id}; repo path was not saved: ${saveErr.message}`,
                true,
            );
        }
        await loadHistory();
        selectJob(r.job_id);
    } catch (e) {
        const message = e.message || "Run submission failed.";
        setSubmitFeedback("error", message);
        toast(message, true);
    } finally {
        submitRunInFlight = false;
        setSubmitLoading(false);
    }
});

// Cancel
async function requestCancel() {
    if (!activeJobId) return;
    try {
        await api(`/runs/${activeJobId}/cancel`, {
            method: "POST",
        });
        toast("Cancel requested");
    } catch (e) {
        toast(e.message, true);
    }
}
$("#cancel-btn").addEventListener("click", () => {
    if (confirm("Cancel this run?")) requestCancel();
});
$("#eval-cancel-btn").addEventListener("click", () => {
    if (confirm("Cancel this evaluation run?")) requestCancel();
});
$$("#story-source-tabs .story-source-tab").forEach((btn) => {
    btn.addEventListener("click", () =>
        setStorySource(btn.dataset.storySource),
    );
});
setStorySource("manual");

// Job selection / SSE
function showRunAlert(title, message, meta = [], surface = "runs") {
    const ui = runSurface(surface);
    $(ui.alertTitle).textContent = title;
    $(ui.alertMsg).textContent = message;
    $(ui.alertMeta).textContent = meta.length
        ? meta.join(" · ")
        : "";
    $(ui.alert).classList.add("show");
}
function hideRunAlert(surface = "runs") {
    const ui = runSurface(surface);
    $(ui.alert).classList.remove("show");
    $(ui.alertTitle).textContent = "";
    $(ui.alertMsg).textContent = "";
    $(ui.alertMeta).textContent = "";
}
function setTermEmpty(message, isError = false, surface = "runs") {
    $(runSurface(surface).term).innerHTML =
        `<div class="empty${isError ? " error" : ""}">${escapeHtml(message)}</div>`;
}
function getFailedStage(events) {
    for (let i = events.length - 1; i >= 0; i--) {
        const ev = events[i];
        if (
            ev.type === "stage.end" &&
            ev.status === "error" &&
            ev.stage
        )
            return ev.stage;
    }
    return null;
}
function getLastErrorEvent(events) {
    for (let i = events.length - 1; i >= 0; i--) {
        const ev = events[i];
        if (
            isPythonLogEvent(ev) &&
            ev.level === "error" &&
            ev.msg
        )
            return ev;
    }
    return null;
}
function updateRunFeedback(job, events, surface = "runs") {
    hideRunAlert(surface);
    if (job.status !== "failed") return;
    const failedStage = getFailedStage(events);
    const lastError = getLastErrorEvent(events);
    const message =
        job.error_message ||
        (lastError && lastError.msg) ||
        (job.exit_code != null
            ? `Run exited with code ${job.exit_code} before detailed logs were captured.`
            : "Run failed before detailed logs were captured.");
    const meta = [];
    if (job.exit_code != null) meta.push(`exit ${job.exit_code}`);
    if (failedStage) meta.push(`stage ${failedStage}`);
    if (!events.length) meta.push("no structured logs");
    showRunAlert("Run failed", message, meta, surface);
}
function clearTerm(surface = "runs") {
    const ui = runSurface(surface);
    $(ui.term).innerHTML = "";
    RUN_TERMINAL_TRIMMED_LOGS[surface] = 0;
    hideRunAlert(surface);
    activeStageGroup = null;
    setActiveSurfaceJob(surface, null);
    if (surface === "runs") {
        clearWorkflowTicker();
        activeRunEvents = [];
        renderRunWorkflow();
    }
    hideOpikLink(surface);
    resetTraceSummary(surface);
    lastSeq = 0;
    ui.metrics.forEach((id) => ($("#" + id).textContent = "—"));
}
function hideOpikLink(surface = "runs") {
    const ui = runSurface(surface);
    const link = $(ui.opikLink);
    const hint = $(ui.opikHint);
    link.style.display = "none";
    link.removeAttribute("href");
    link.title = "";
    hint.style.display = "none";
    hint.textContent = "";
}
function updateOpikLink(job, surface = "runs") {
    const dashboardUrl = job?.opik?.dashboard_url;
    const ui = runSurface(surface);
    const link = $(ui.opikLink);
    const hint = $(ui.opikHint);
    if (!dashboardUrl) {
        hideOpikLink(surface);
        if (job) {
            hint.textContent =
                "Configure Opik in Settings to open traces for this run.";
            hint.style.display = "";
        }
        return;
    }
    link.href = dashboardUrl;
    link.title = `Open Opik traces filtered to ${job.opik.thread_id || job.change_id}`;
    hint.innerHTML = `<span class="filter-chip">Traces filtered to ${job.opik.thread_id || job.change_id}</span>`;
    hint.style.display = "";
    link.style.display = "";
}
function resetTraceSummary(surface = "runs") {
    TRACE_EVENTS[surface] = [];
    const ui = runSurface(surface);
    const summary = $(ui.traceSummary);
    const meta = $(ui.traceSummaryMeta);
    const timeline = $(ui.traceTimeline);
    if (summary) summary.classList.remove("show");
    if (meta) meta.textContent = "";
    if (timeline) timeline.innerHTML = "";
}
function updateTraceSummary(surface = "runs") {
    const events = TRACE_EVENTS[surface] || [];
    const ui = runSurface(surface);
    const summary = $(ui.traceSummary);
    const meta = $(ui.traceSummaryMeta);
    const timeline = $(ui.traceTimeline);
    if (!summary || !meta || !timeline) return;
    if (!events.length) {
        summary.classList.remove("show");
        meta.textContent = "";
        timeline.innerHTML = "";
        return;
    }
    const starts = events.filter((ev) => ev.type === "opik.start");
    const ends = events.filter((ev) => ev.type === "opik.end");
    const errors = ends.filter(
        (ev) => ev.status === "error",
    ).length;
    const running = Math.max(0, starts.length - ends.length);
    meta.textContent = `${starts.length} started · ${ends.length} finished${running ? ` · ${running} running` : ""}${errors ? ` · ${errors} error${errors === 1 ? "" : "s"}` : ""}`;
    timeline.innerHTML = "";
    starts.slice(-8).forEach((start) => {
        const end = [...ends]
            .reverse()
            .find(
                (candidate) =>
                    candidate.name === start.name &&
                    candidate.depth === start.depth,
            );
        const pill = document.createElement("span");
        pill.className = "trace-pill";
        if (end?.status === "error") pill.classList.add("error");
        else if (!end) pill.classList.add("running");
        const label = start.name || "trace";
        pill.textContent = `${start.kind === "trace" ? "◎" : "◦"} ${label.replace(/^stage:/, "")}`;
        pill.title = `${label}${end?.duration_ms != null ? ` · ${end.duration_ms}ms` : ""}`;
        timeline.appendChild(pill);
    });
    summary.classList.add("show");
}
function collectTraceEvent(ev, surface = "runs") {
    if (ev.type !== "opik.start" && ev.type !== "opik.end") return;
    TRACE_EVENTS[surface].push(ev);
    updateTraceSummary(surface);
}
function formatEventTime(ts) {
    return fmtLocalClockTime(ts);
}
function buildEventRow(cls, tag, msg, ts, indent = 0) {
    const div = document.createElement("div");
    div.className = cls;
    if (indent > 0) div.style.paddingLeft = `${indent * 14}px`;
    const tsEl = document.createElement("span");
    tsEl.className = "ts";
    tsEl.textContent = ts;
    const tagEl = document.createElement("span");
    tagEl.className = "tag";
    tagEl.textContent = tag;
    const msgEl = document.createElement("span");
    msgEl.className = "msg";
    msgEl.textContent = msg;
    div.append(tsEl, tagEl, msgEl);
    return div;
}
function formatTraceMetadata(meta) {
    if (!meta || typeof meta !== "object" || Array.isArray(meta))
        return "";
    const preferred = [
        "stage",
        "runner",
        "agent",
        "uow_id",
        "iteration",
        "intake_mode",
        "model",
        "change_id",
    ];
    const parts = [];
    const seen = new Set();
    preferred.forEach((key) => {
        const value = meta[key];
        if (value == null || value === "") return;
        parts.push(`${key}=${value}`);
        seen.add(key);
    });
    Object.entries(meta).forEach(([key, value]) => {
        if (seen.has(key) || value == null || value === "") return;
        parts.push(`${key}=${value}`);
    });
    return parts.join(" · ");
}
function describeEvent(ev, surface = "runs", mode = "live") {
    const ui = runSurface(surface);
    let cls = "ev";
    let tag = ev.type;
    let msg = "";
    if (ev.type === "job.start") {
        cls += " stage";
        msg = ` ${ev.change_id || ""}${ev.runner ? ` · ${ev.runner}` : ""}${ev.model ? `/${ev.model}` : ""}`;
    } else if (ev.type === "stage.start" || ev.type === "stage.end") {
        cls += " stage";
        tag = `▾ ${ev.stage}`;
        msg = ev.type === "stage.end" ? ` (${ev.status})` : "";
    } else if (ev.type === "cli.invoke" || ev.type === "cli.exit") {
        cls += " cli";
        msg =
            ev.type === "cli.exit"
                ? ` exit=${ev.exit_code} ${ev.duration_ms}ms`
                : ` ${(ev.cmd || []).join(" ")} (${ev.argc || 0} args)`;
    } else if (ev.type === "log") {
        const lvl = (ev.level || "").toLowerCase();
        cls += " log-" + lvl;
        cls +=
            lvl === "error" ||
            lvl === "warning" ||
            lvl === "critical"
                ? " stderr"
                : " stdout";
        tag = ev.level ? ev.level.toUpperCase() : "LOG";
        const loggerName = ev.logger ? `${ev.logger}: ` : "";
        msg = loggerName + (ev.line || ev.msg || "");
    } else if (ev.type === "cli.stderr") {
        cls += " stderr";
        msg = ev.line || "";
    } else if (ev.type === "opik.start" || ev.type === "opik.end") {
        cls += " trace";
        if (ev.status === "error") cls += " error";
        tag = `${ev.kind === "span" ? "◦" : "◎"} ${ev.name}`;
        const meta = formatTraceMetadata(ev.metadata);
        if (ev.type === "opik.start") {
            msg = ` ${ev.kind} · ${ev.trace_type}${meta ? ` · ${meta}` : ""}`;
        } else {
            msg = ` ${ev.status || "ok"}${ev.duration_ms != null ? ` · ${ev.duration_ms}ms` : ""}${meta ? ` · ${meta}` : ""}${ev.error ? ` · ${ev.error}` : ""}`;
        }
    } else if (ev.type === "llm.call") {
        cls += ev.status === "ok" ? " stdout" : " stderr";
        tag = `${ev.runner || "llm"}${ev.agent ? `/${ev.agent}` : ""}`;
        const parts = [
            ev.status || "unknown",
            ev.model,
            ev.duration_ms != null ? `${ev.duration_ms}ms` : "",
            ev.tokens_in != null || ev.tokens_out != null
                ? `${ev.tokens_in || 0} in / ${ev.tokens_out || 0} out`
                : "",
            ev.cost_usd != null ? formatMoney(Number(ev.cost_usd)) : "",
            ev.error_category,
        ].filter(Boolean);
        msg = ` ${parts.join(" · ")}`;
    } else if (ev.type === "job.end") {
        cls += " end";
        msg = ` ${ev.status} (exit ${ev.exit_code})`;
    } else if (ev.type === "user.response") {
        cls += " stdout";
        tag = "✓ user.response";
        msg = ` Response received${ev.conversation_id ? ` (${ev.conversation_id})` : ""} — workflow continuing.`;
    } else if (ev.type === "user.prompt.timeout") {
        cls += " stderr";
        tag = "⚠ user.prompt.timeout";
        msg =
            " No response received — continuing with open questions.";
    } else if (ev.type === "metrics") {
        if (mode === "live") renderSurfaceMetrics(surface);
        cls += " trace";
        tag = "metrics";
        const parts = [
            ev.tokens_in != null ? `${ev.tokens_in} in` : "",
            ev.tokens_out != null ? `${ev.tokens_out} out` : "",
            ev.cost_usd != null ? formatMoney(Number(ev.cost_usd)) : "",
        ].filter(Boolean);
        msg = parts.length ? ` ${parts.join(" · ")}` : " updated";
    }
    return { cls, tag, msg };
}
function ensureTermReady(surface = "runs") {
    const term = $(runSurface(surface).term);
    if (term.querySelector(".empty")) term.innerHTML = "";
    return term;
}
function startStageGroup(term, ev) {
    const header = buildEventRow(
        "ev stage stage-header",
        `▾ ${ev.stage}`,
        "",
        formatEventTime(ev.ts),
    );
    const children = document.createElement("div");
    children.className = "stage-children";
    const group = document.createElement("div");
    group.className = "stage-group";
    group.append(header, children);
    term.appendChild(group);
    activeStageGroup = { name: ev.stage, group, header, children };
}
function finishStageGroup(ev) {
    if (!activeStageGroup || activeStageGroup.name !== ev.stage)
        return false;
    activeStageGroup.group.classList.add(
        ev.status === "error" ? "error" : "ok",
    );
    activeStageGroup.header.querySelector(".msg").textContent =
        ` (${ev.status})`;
    activeStageGroup = null;
    return true;
}
function shouldNestInStage(ev) {
    return (
        !!activeStageGroup &&
        ![
            "job.start",
            "job.end",
            "stage.start",
            "stage.end",
            "metrics",
            "stream.end",
        ].includes(ev.type)
    );
}
function appendFlatEvent(
    parent,
    ev,
    surface = "runs",
    mode = "live",
) {
    const details = describeEvent(ev, surface, mode);
    if (!details) return null;
    const row = buildEventRow(
        details.cls,
        details.tag,
        details.msg,
        formatEventTime(ev.ts),
        Number(ev.depth || 0),
    );
    row.dataset.terminalLogRow = "true";
    parent.appendChild(row);
    return row;
}
function renderTermLogLimitNotice(term, surface = "runs") {
    const trimmed = RUN_TERMINAL_TRIMMED_LOGS[surface] || 0;
    let note = term.querySelector(".log-limit-notice");
    if (!trimmed) {
        if (note) note.remove();
        return;
    }
    if (!note) {
        note = document.createElement("div");
        note.className = "log-limit-notice";
        term.prepend(note);
    }
    note.textContent = `${trimmed} older terminal event${trimmed === 1 ? "" : "s"} hidden to keep the UI responsive. Showing the latest ${RUN_TERMINAL_VISIBLE_LOG_LIMIT}.`;
}
function enforceTermLogLimit(term, surface = "runs") {
    const rows = Array.from(
        term.querySelectorAll('[data-terminal-log-row="true"]'),
    );
    const extra = rows.length - RUN_TERMINAL_VISIBLE_LOG_LIMIT;
    if (extra <= 0) return;
    rows.slice(0, extra).forEach((row) => row.remove());
    RUN_TERMINAL_TRIMMED_LOGS[surface] =
        (RUN_TERMINAL_TRIMMED_LOGS[surface] || 0) + extra;
    renderTermLogLimitNotice(term, surface);
}
function appendEvent(ev, surface = "runs", mode = "live") {
    const ui = runSurface(surface);
    collectTraceEvent(ev, surface);
    if (ev.stage) {
        $(ui.stage).textContent = ev.stage;
        $(ui.stage).classList.remove("metrics-skeleton");
    }
    if (ev.type === "metrics" && mode === "live") {
        renderSurfaceMetrics(surface);
    }
    if (ev.type === "job.end") activeStageGroup = null;
    if (ev.seq) lastSeq = ev.seq;

    if (ev.type === "user.prompt") {
        const term = ensureTermReady(surface);
        const form = buildUserPromptForm(ev);
        form.dataset.terminalLogRow = "true";
        term.appendChild(form);
        enforceTermLogLimit(term, surface);
        term.scrollTop = term.scrollHeight;
        return;
    }
    if (!isTerminalVisibleEvent(ev)) return;

    const term = ensureTermReady(surface);
    appendFlatEvent(term, ev, surface, mode);
    enforceTermLogLimit(term, surface);
    term.scrollTop = term.scrollHeight;
}
function escapeHtml(s) {
    return String(s).replace(
        /[&<>]/g,
        (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c],
    );
}
function buildUserPromptForm(ev) {
    return buildUserEscalationCard(ev);
}
function buildUserEscalationCard(ev) {
    const questions = Array.isArray(ev.questions)
        ? ev.questions
        : [];
    const wrap = document.createElement("div");
    wrap.className = "ev user-prompt-block";
    wrap.dataset.conversationId = ev.conversation_id || "";
    wrap.dataset.escalationId = ev.escalation_id || "";

    // Header with agent name and severity badge
    const header = document.createElement("div");
    header.className = "user-prompt-header";
    const agentLabel = ev.agent || "Agent";
    const stageLabel = ev.stage ? ` · ${ev.stage}` : "";
    const uowLabel = ev.uow_id ? ` · ${ev.uow_id}` : "";
    header.innerHTML = `<strong>${escapeHtml(agentLabel)}</strong>${escapeHtml(stageLabel)}${escapeHtml(uowLabel)} needs your input`;
    if (ev.severity) {
        const badge = document.createElement("span");
        badge.className = `severity-badge severity-${ev.severity}`;
        badge.textContent = ev.severity;
        badge.style.cssText =
            "margin-left:8px;padding:2px 8px;border-radius:4px;font-size:0.75em;font-weight:600;text-transform:uppercase;" +
            (ev.severity === "blocking"
                ? "background:#fee2e2;color:#991b1b;"
                : ev.severity === "approval"
                  ? "background:#fef3c7;color:#92400e;"
                  : "background:#e0e7ff;color:#3730a3;");
        header.appendChild(badge);
    }
    wrap.appendChild(header);

    // Title
    if (ev.title) {
        const titleEl = document.createElement("div");
        titleEl.style.cssText = "font-weight:600;margin:8px 0 4px;";
        titleEl.textContent = ev.title;
        wrap.appendChild(titleEl);
    }

    // Message
    if (ev.message) {
        const msgEl = document.createElement("div");
        msgEl.style.cssText =
            "margin-bottom:8px;white-space:pre-wrap;";
        msgEl.textContent = ev.message;
        wrap.appendChild(msgEl);
    }

    if (!questions.length) {
        const p = document.createElement("div");
        p.textContent = "(no questions)";
        wrap.appendChild(p);
        return wrap;
    }

    const form = document.createElement("form");
    form.className = "user-prompt-form";
    questions.forEach((q, i) => {
        const qObj =
            typeof q === "string"
                ? { id: `q${i + 1}`, label: q, kind: "textarea" }
                : q;
        const qDiv = document.createElement("div");
        qDiv.className = "prompt-q";
        const label = document.createElement("label");
        label.textContent = `${i + 1}. ${qObj.label || qObj.id}`;
        let input;
        if (qObj.kind === "boolean") {
            input = document.createElement("select");
            input.innerHTML =
                '<option value="">-- select --</option><option value="yes">Yes</option><option value="no">No</option>';
        } else {
            input = document.createElement("textarea");
            input.rows = 2;
            input.placeholder = "Your answer...";
        }
        input.dataset.questionId = qObj.id || `q${i + 1}`;
        input.dataset.question = qObj.label || qObj.id;
        if (qObj.required) input.required = true;
        qDiv.append(label, input);
        form.appendChild(qDiv);
    });

    // Free-form message box
    const msgDiv = document.createElement("div");
    msgDiv.className = "prompt-q";
    const msgLabel = document.createElement("label");
    msgLabel.textContent = "Additional message (optional):";
    const msgTa = document.createElement("textarea");
    msgTa.rows = 2;
    msgTa.placeholder = "Any additional context...";
    msgTa.dataset.questionId = "__message__";
    msgDiv.append(msgLabel, msgTa);
    form.appendChild(msgDiv);

    const btn = document.createElement("button");
    btn.type = "submit";
    btn.textContent = "Submit Response";
    btn.className = "prompt-submit";
    form.appendChild(btn);
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const responses = {};
        form.querySelectorAll("textarea, select").forEach((el) => {
            if (
                el.dataset.questionId &&
                el.dataset.questionId !== "__message__"
            )
                responses[el.dataset.questionId] = el.value;
        });
        const message =
            (
                form.querySelector(
                    '[data-question-id="__message__"]',
                ) || {}
            ).value || "";
        btn.disabled = true;
        btn.textContent = "Submitting...";
        const payload = {
            conversation_id: ev.conversation_id || undefined,
            escalation_id: ev.escalation_id || undefined,
            message: message || undefined,
            responses,
        };
        try {
            await api(`/runs/${activeJobId}/respond`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            form.innerHTML = `<div class="prompt-submitted">✓ Response submitted — workflow continuing.</div>`;
        } catch (err) {
            btn.disabled = false;
            btn.textContent = "Submit Response";
            toast(
                (err && err.message) || "Failed to submit response",
                true,
            );
        }
    });
    wrap.appendChild(form);
    return wrap;
}

async function selectJob(id, surface = "runs") {
    if (activeStream) {
        activeStream.close();
        activeStream = null;
    }
    const ui = runSurface(surface);
    activeJobId = id;
    clearTerm(surface);
    const historySelect = $("#history");
    if (historySelect && historySelect.value !== id)
        historySelect.value = id;
    try {
        const job = await api(`/runs/${id}`);
        setActiveSurfaceJob(surface, job);
        const dot =
            job.status === "running" || job.status === "queued"
                ? `<span class="status-dot ${job.status}"></span>`
                : "";
        $(ui.title).innerHTML =
            `${dot}${job.id} · ${job.change_id} · <span class="status ${job.status}">${STATUS_ICON[job.status] || ""}${job.status}</span>`;
        updateOpikLink(job, surface);
        $(ui.cancel).style.display =
            job.status === "running" ||
            job.status === "queued" ||
            job.status === "awaiting_input"
                ? ""
                : "none";
        renderSurfaceMetrics(surface);
        const stageEl = $("#" + ui.metrics[3]);
        if (job.current_stage) {
            stageEl.textContent = job.current_stage;
            stageEl.classList.remove("metrics-skeleton");
        } else {
            stageEl.textContent = "";
            stageEl.classList.add("metrics-skeleton");
        }
        // Replay past events then attach stream
        const events = await api(
            `/runs/${id}/events?limit=${RUN_REPLAY_EVENT_LIMIT}`,
        );
        if (surface === "runs") {
            activeRunEvents = Array.isArray(events)
                ? events
                      .filter(isWorkflowHistoryEvent)
                      .slice(-RUN_WORKFLOW_EVENT_LIMIT)
                : [];
            renderRunWorkflow(activeRunJob, activeRunEvents);
        }
        updateRunFeedback(job, events, surface);
        if (!hasVisibleTerminalEvent(events)) {
            if (job.status === "failed")
                setTermEmpty(
                    "No terminal-visible run events were captured. Review the failure summary above.",
                    true,
                    surface,
                );
            else if (
                job.status === "running" ||
                job.status === "queued"
            )
                setTermEmpty("Waiting for run events...", false, surface);
            else
                setTermEmpty(
                    "No terminal-visible run events were captured.",
                    false,
                    surface,
                );
        }
        events.forEach((ev) => appendEvent(ev, surface, "replay"));
        if (
            job.status === "running" ||
            job.status === "queued" ||
            job.status === "awaiting_input"
        ) {
            const es = new EventSource(
                `/runs/${id}/stream${lastSeq ? `?after=${lastSeq}` : ""}`,
            );
            activeStream = es;
            const onStreamEvent = (m) => {
                try {
                    const ev = JSON.parse(m.data);
                    if (ev.type !== "stream.end") {
                        const nextJob = applyWorkflowEventToJob(
                            getActiveSurfaceJob(surface),
                            ev,
                        );
                        if (nextJob)
                            setActiveSurfaceJob(surface, nextJob);
                    }
                    if (
                        surface === "runs" &&
                        ev.type !== "stream.end" &&
                        rememberWorkflowEvent(ev)
                    ) {
                        renderRunWorkflow(
                            activeRunJob,
                            activeRunEvents,
                        );
                    }
                    appendEvent(ev, surface);
                    if (ev.type === "stream.end") {
                        es.close();
                        activeStream = null;
                        return;
                    }
                    if (ev.type === "job.end") {
                        es.close();
                        activeStream = null;
                        $(ui.cancel).style.display = "none";
                        loadHistory();
                        if (surface === "evaluate") loadEvaluate();
                        setTimeout(() => {
                            if (activeJobId === id)
                                selectJob(id, surface);
                        }, 150);
                    }
                } catch {}
            };
            STREAM_EVENT_TYPES.forEach((type) =>
                es.addEventListener(type, onStreamEvent),
            );
            es.onmessage = onStreamEvent;
            let _sseRetried = false;
            es.onerror = () => {
                es.close();
                activeStream = null;
                if (
                    !_sseRetried &&
                    activeJobId === id &&
                    RUN_ACTIVE_STATUSES.has(activeRunJob?.status)
                ) {
                    _sseRetried = true;
                    setTimeout(() => {
                        if (
                            activeJobId !== id ||
                            !RUN_ACTIVE_STATUSES.has(
                                activeRunJob?.status,
                            )
                        )
                            return;
                        const es2 = new EventSource(
                            `/runs/${id}/stream${lastSeq ? `?after=${lastSeq}` : ""}`,
                        );
                        activeStream = es2;
                        STREAM_EVENT_TYPES.forEach((type) =>
                            es2.addEventListener(
                                type,
                                onStreamEvent,
                            ),
                        );
                        es2.onmessage = onStreamEvent;
                        es2.onerror = () => {
                            es2.close();
                            activeStream = null;
                        };
                    }, 2000);
                }
            };
        }
        loadHistory();
    } catch (e) {
        toast(e.message, true);
    }
}

// Agents
