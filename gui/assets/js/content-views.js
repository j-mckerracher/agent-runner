/*
 * Secondary content views: Agents, Evaluation Results, and Run Evaluations.
 *
 * These screens mostly render data returned by the API and reuse shared helpers
 * from app-core.js and run selection helpers from runs.js.
 */

function setAgentPromptEmpty(
    message = "Select an agent to view its latest prompt.",
) {
    $("#agent-detail-title").textContent = "Select an agent";
    $("#agent-detail-meta").textContent =
        "Click an agent to view its latest prompt.";
    const path = $("#agent-detail-path");
    path.style.display = "none";
    path.textContent = "";
    $("#agent-detail-tags").innerHTML = "";
    $("#agent-prompt-body").innerHTML =
        `<span style="color:var(--t3)">${escapeHtml(message)}</span>`;
}
function setActiveAgent(name) {
    activeAgentName = name || null;
    $$("#agents-list .agent-row").forEach((row) =>
        row.classList.toggle(
            "active",
            row.dataset.agent === activeAgentName,
        ),
    );
}
function renderAgentTags(tags) {
    const wrap = $("#agent-detail-tags");
    wrap.innerHTML = "";
    (tags || []).forEach((tag) => {
        const el = document.createElement("span");
        el.className = "agent-tag";
        el.textContent = tag;
        wrap.appendChild(el);
    });
}
async function selectAgent(name) {
    setActiveAgent(name);
    try {
        const detail = await api(
            `/agents/${encodeURIComponent(name)}`,
        );
        $("#agent-detail-title").textContent = detail.name;
        $("#agent-detail-meta").textContent =
            `latest ${detail.version} · hash ${detail.bundle_hash}`;
        const path = $("#agent-detail-path");
        if (detail.prompt_file) {
            path.style.display = "";
            path.textContent = `File: ${detail.prompt_file}`;
        } else {
            path.style.display = "none";
            path.textContent = "";
        }
        renderAgentTags(detail.tags);
        const rawMd = detail.prompt_text || "Prompt file is empty.";
        $("#agent-prompt-body").innerHTML =
            typeof marked !== "undefined"
                ? marked.parse(rawMd)
                : `<pre style="white-space:pre-wrap">${escapeHtml(rawMd)}</pre>`;
    } catch (e) {
        setActiveAgent(null);
        setAgentPromptEmpty(
            "Unable to load the selected agent prompt.",
        );
        toast(e.message, true);
    }
}
async function loadAgents() {
    const r = await api("/agents");
    const wrap = $("#agents-list");
    wrap.innerHTML = "";
    if (!r.items.length) {
        wrap.innerHTML =
            '<div class="empty">No materialized agents found in agent-definition-source/.</div>';
        setActiveAgent(null);
        setAgentPromptEmpty(
            "No materialized agents found in agent-definition-source/.",
        );
    }
    r.items.forEach((a) => {
        const d = document.createElement("div");
        d.className =
            "agent-row" +
            (a.name === activeAgentName ? " active" : "");
        d.dataset.agent = a.name;
        d.innerHTML = `<h3>${escapeHtml(a.name)}</h3><div class="meta">latest ${escapeHtml(a.version)} · hash ${escapeHtml(a.bundle_hash)} · tags: ${escapeHtml((a.tags || []).join(", ") || "—")}</div>`;
        d.addEventListener("click", () => selectAgent(a.name));
        wrap.appendChild(d);
    });
    if (
        activeAgentName &&
        !r.items.some((a) => a.name === activeAgentName)
    ) {
        setActiveAgent(null);
        setAgentPromptEmpty();
    }
    $("#agents-meta").textContent = `${r.count} agents`;
}

// Corpus
async function loadCorpus() {
    const r = await api("/corpus");
    const wrap = $("#corpus-list");
    wrap.innerHTML = "";
    if (!r.items.length) {
        wrap.innerHTML =
            '<div class="empty">No evaluation results found. Run an evaluation to see results here.</div>';
    }
    r.items.forEach((s) => {
        const d = document.createElement("div");
        d.className = "item";
        d.style.cursor = "pointer";
        const pr =
            s.pass_rate == null
                ? "—"
                : Math.round(s.pass_rate) + "%";
        const difficulty = s.difficulty
            ? `${escapeHtml(s.difficulty.toUpperCase())} · `
            : "";
        const lastStatus = s.last_status
            ? ` · last: ${escapeHtml(s.last_status)}`
            : "";
        d.innerHTML = `<h3>${escapeHtml(s.title || s.id)}</h3><div class="meta">${difficulty}runs: ${s.runs || 0} · pass rate: ${pr} · ACs: ${s.ac_count || 0}${lastStatus}</div>`;
        d.addEventListener("click", () => loadCorpusDetail(s.id));
        wrap.appendChild(d);
    });
    $("#corpus-meta").textContent = `${r.count} stories`;
}
async function loadCorpusDetail(changeId) {
    const detail = $("#corpus-detail");
    if (!detail) return;
    try {
        const story = await api(`/corpus/${changeId}`);
        const history = (story.history || []).slice(0, 5);
        const acs = (story.acceptance_criteria || [])
            .map((ac) => {
                const id = escapeHtml(ac.id || "AC-?");
                const text = escapeHtml(ac.text || String(ac));
                return `<li><strong>${id}:</strong> ${text}</li>`;
            })
            .join("");
        const historyHtml = history
            .map(
                (h) =>
                    `<tr><td class="mono">${escapeHtml(h.status || "unknown")}</td><td class="mono">${escapeHtml(h.submitted_at || "—")}</td><td class="mono">${escapeHtml(h.id || "—")}</td></tr>`,
            )
            .join("");
        detail.innerHTML = `
      <h3>${escapeHtml(story.title || story.id)}</h3>
      <p>${escapeHtml(story.description || "No description available.")}</p>
      <div class="row2" style="margin:12px 0">
        <div><span style="color:var(--t3)">Runs:</span> <strong>${story.runs || 0}</strong></div>
        <div><span style="color:var(--t3)">Pass rate:</span> <strong>${story.pass_rate != null ? Math.round(story.pass_rate) + "%" : "—"}</strong></div>
      </div>
      <h4>Acceptance criteria</h4>
      <ul>${acs || "<li>No criteria recorded.</li>"}</ul>
      <h4>Recent job history</h4>
      ${historyHtml ? `<table style="width:100%;font-size:11px;color:var(--t2)"><tr><th>Status</th><th>Submitted</th><th>Job ID</th></tr>${historyHtml}</table>` : "<p>No job history available.</p>"}
    `;
        detail.classList.add("show");
    } catch (e) {
        detail.innerHTML = `<div class="empty error">Failed to load story detail: ${escapeHtml(e.message)}</div>`;
        detail.classList.add("show");
    }
}

// Evaluate
function updateEvaluationStorySelect(rows) {
    const sel = $("#eval-story");
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = "";
    rows.forEach((t) => {
        const o = document.createElement("option");
        o.value = t.task;
        o.textContent = `${t.task} — ${t.title || ""}`;
        sel.appendChild(o);
    });
    if (current && rows.some((t) => t.task === current))
        sel.value = current;
}
function updateEvaluateOpikLink(cfg) {
    const link = $("#eval-opik-link");
    const missing = $("#eval-opik-missing");
    const url = buildOpikProjectUrl(cfg?.opik, {
        tab: "experiments",
    });
    if (!url) {
        link.style.display = "none";
        link.removeAttribute("href");
        missing.style.display = "";
        return;
    }
    link.href = url;
    link.title =
        "Open the Opik project workspace for evaluations and experiments";
    link.style.display = "";
    missing.style.display = "none";
}
function currentEvalShaValue() {
    const select = $("#eval-sha");
    const custom = $("#eval-sha-custom");
    if (!select) return "";
    if (select.value === EVAL_CUSTOM_SHA_OPTION)
        return custom?.value.trim() || "";
    return select.value.trim();
}
function syncEvalShaCustomInput() {
    const select = $("#eval-sha");
    const custom = $("#eval-sha-custom");
    if (!select || !custom) return;
    const useCustom = select.value === EVAL_CUSTOM_SHA_OPTION;
    custom.hidden = !useCustom;
    custom.disabled = !useCustom;
    custom.required = useCustom;
}
function updateEvalShaOptions(bootstrapSha) {
    const select = $("#eval-sha");
    const custom = $("#eval-sha-custom");
    if (!select || !custom) return;
    const current = currentEvalShaValue();
    EVAL_BOOTSTRAP_SHA = (bootstrapSha || "").trim();
    select.innerHTML = "";
    if (EVAL_BOOTSTRAP_SHA) {
        const bootstrapOption = document.createElement("option");
        bootstrapOption.value = EVAL_BOOTSTRAP_SHA;
        bootstrapOption.textContent = `${EVAL_BOOTSTRAP_SHA} (bootstrap)`;
        select.appendChild(bootstrapOption);
    }
    const customOption = document.createElement("option");
    customOption.value = EVAL_CUSTOM_SHA_OPTION;
    customOption.textContent = EVAL_BOOTSTRAP_SHA
        ? "Custom SHA…"
        : "Enter SHA manually…";
    select.appendChild(customOption);
    if (current) {
        if (EVAL_BOOTSTRAP_SHA && current === EVAL_BOOTSTRAP_SHA) {
            select.value = EVAL_BOOTSTRAP_SHA;
        } else {
            select.value = EVAL_CUSTOM_SHA_OPTION;
            custom.value = current;
        }
    } else if (EVAL_BOOTSTRAP_SHA) {
        select.value = EVAL_BOOTSTRAP_SHA;
    } else {
        select.value = EVAL_CUSTOM_SHA_OPTION;
    }
    syncEvalShaCustomInput();
}
let _evalStories = [];
function renderEvalStoryPreview() {
    const preview = $("#eval-story-preview");
    if (!preview) return;
    const sel = $("#eval-difficulty");
    if (!sel) return;
    const selected = Array.from(sel.selectedOptions).map(
        (o) => o.value,
    );
    const stories = _evalStories.filter((s) =>
        selected.includes(s.difficulty),
    );
    if (!stories.length) {
        preview.hidden = true;
        return;
    }
    preview.hidden = false;
    preview.innerHTML = stories
        .map(
            (s) => `
    <h4>${escapeHtml(s.difficulty.toUpperCase())}: ${escapeHtml(s.title)}</h4>
    <p>${escapeHtml(s.description)}</p>
  `,
        )
        .join("");
}
async function loadEvaluate() {
    const [r, cfg] = await Promise.all([
        api("/evaluate/summary"),
        api("/settings"),
    ]);
    try {
        const sr = await api("/evaluate/stories");
        _evalStories = sr.items || [];
    } catch (e) {
        _evalStories = [];
    }
    const diffSel = $("#eval-difficulty");
    if (diffSel) {
        diffSel.addEventListener("change", renderEvalStoryPreview);
        renderEvalStoryPreview();
    }
    updateEvaluateOpikLink(cfg);
    updateEvalShaOptions(cfg.eval_bootstrap?.target_sha || "");
    updateEvalBaselineSelect(r.reports || []);
    const cards = $("#eval-cards");
    const rows = r.rows || [];
    cards.innerHTML = `
    <div class="card"><div class="lbl">Verdict</div><div class="val">${escapeHtml(r.verdict || "insufficient data")}</div></div>
    <div class="card"><div class="lbl">Quality</div><div class="val">${formatPercent(r.quality_score)}</div></div>
    <div class="card"><div class="lbl">Reliability</div><div class="val">${formatPercent(r.pass_rate)}</div></div>
    <div class="card"><div class="lbl">Wall time</div><div class="val">${formatSeconds(r.wall_seconds_mean)}</div></div>
    <div class="card"><div class="lbl">Tokens</div><div class="val">${formatNumber(r.tokens_total_mean)}</div></div>
  `;
    const warningBox = $("#eval-warnings");
    const warnings = r.warnings || [];
    if (warnings.length) {
        warningBox.classList.add("show");
        warningBox.innerHTML = warnings
            .map((w) => `<div>⚠ ${escapeHtml(w)}</div>`)
            .join("");
    } else {
        warningBox.classList.remove("show");
        warningBox.innerHTML = "";
    }
    const list = $("#eval-list");
    list.innerHTML = "";
    if (!rows.length) {
        list.innerHTML =
            '<div class="empty">No benchmark reports found yet.</div>';
        $("#eval-detail").classList.remove("show");
        $("#eval-detail").innerHTML = "";
    }
    rows.forEach((t, idx) => {
        const d = document.createElement("div");
        d.className = "item";
        const cur = formatPercent(t.current);
        const reliability = formatPercent(t.pass_rate);
        const status =
            t.status === "failed"
                ? ` · <span style="color:var(--er)">FAILED</span>`
                : ` · ${escapeHtml(t.status || "unknown")}`;
        const warnings = (t.warnings || []).length
            ? ` · ${escapeHtml((t.warnings || []).join("; "))}`
            : "";
        d.innerHTML = `<h3>${escapeHtml(t.task)}</h3><div class="meta">${escapeHtml(t.title || "")} · AC score ${cur} · reliability ${reliability} · runs ${t.runs} · wall ${formatSeconds(t.wall_seconds_mean)} · tokens ${formatNumber(t.tokens_total_mean)}${status}${warnings}</div>`;
        d.addEventListener("click", () => renderEvalDetail(t));
        list.appendChild(d);
        if (idx === 0) renderEvalDetail(t);
    });
    $("#eval-meta").textContent =
        `${rows.length} benchmark${rows.length === 1 ? "" : "s"}`;
}
function updateEvalBaselineSelect(reports) {
    const sel = $("#eval-baseline");
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML =
        '<option value="">No baseline selected</option>';
    reports.forEach((report) => {
        const opt = document.createElement("option");
        opt.value = report.path || "";
        const score = formatPercent(
            report.summary?.quality?.weighted_score,
        );
        opt.textContent = `${report.name || "report"} · ${report.runner || "runner"}${report.model ? "/" + report.model : ""} · ${score}`;
        sel.appendChild(opt);
    });
    if (Array.from(sel.options).some((o) => o.value === current))
        sel.value = current;
}
function renderEvalDetail(row) {
    const detail = $("#eval-detail");
    const story = row.story || {};
    const trials = row.details || [];
    const criteria = story.acceptance_criteria || [];
    const artifacts = trials.flatMap((t) =>
        Object.entries(t.artifacts || {}).map(
            ([k, v]) => `${k}: ${v}`,
        ),
    );
    const acBlocks = criteria
        .map((ac) => {
            const id = (String(ac).match(/^(AC\d+)/) || [
                "",
                "AC?",
            ])[1];
            const perTrial = trials
                .map((t) => {
                    const acResult =
                        t.hidden_tests?.ac_results?.[id] || {};
                    const cases = acResult.cases || [];
                    const status = acResult.passed
                        ? "pass"
                        : "fail";
                    const messages = cases
                        .filter((c) => c.message)
                        .map((c) => `${c.name}: ${c.message}`);
                    const skipped = cases
                        .filter((c) => c.status === "skipped")
                        .map((c) => c.name);
                    return `<div class="eval-ac ${status}">
        <div class="eval-ac-head">${escapeHtml(id)} · trial ${escapeHtml(String(t.trial_index || 1))} · ${acResult.passed ? "passed" : "failed"}</div>
        <div class="meta">tests: ${escapeHtml((acResult.tests || []).join(", ") || "none")}</div>
        ${messages.length ? `<pre>${escapeHtml(messages.join("\n\n"))}</pre>` : ""}
        ${skipped.length ? `<div class="meta">skipped: ${escapeHtml(skipped.join(", "))}</div>` : ""}
      </div>`;
                })
                .join("");
            return `<h4>${escapeHtml(id)}</h4><p>${escapeHtml(String(ac))}</p>${perTrial}`;
        })
        .join("");
    detail.innerHTML = `
    <h3>${escapeHtml(row.task || "Benchmark")}: ${escapeHtml(row.title || "")}</h3>
    <p>${escapeHtml(story.description || "No story description recorded in this report.")}</p>
    <h4>Acceptance criteria and tests</h4>
    ${acBlocks || "<p>No AC-level detail recorded.</p>"}
    <h4>Artifacts</h4>
    ${artifacts.length ? `<pre>${escapeHtml(artifacts.join("\n"))}</pre>` : "<p>No artifact links recorded.</p>"}
  `;
    detail.classList.add("show");
}
$("#eval-submit-btn").addEventListener("click", async () => {
    const body = {
        repo: $("#eval-repo").value.trim(),
        sha: currentEvalShaValue(),
        runner: $("#eval-runner").value,
        model: $("#eval-model").value || null,
        difficulties: Array.from(
            $("#eval-difficulty").selectedOptions,
        ).map((o) => o.value),
        runs: Number($("#eval-runs").value || 1),
        project_test_command: $("#eval-extra").value || null,
        compare_to: $("#eval-baseline").value || null,
    };
    if (!body.repo || !body.sha) {
        toast("repo and gold-master SHA are required", true);
        return;
    }
    const btn = $("#eval-submit-btn");
    btn.disabled = true;
    try {
        const r = await api("/evaluate/benchmark-runs", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(body),
        });
        try {
            await rememberRepoPath(body.repo);
            toast("Submitted evaluation " + r.job_id);
        } catch (saveErr) {
            toast(
                `Submitted evaluation ${r.job_id}; repo path was not saved: ${saveErr.message}`,
                true,
            );
        }
        await loadEvaluate();
        selectJob(r.job_id, "evaluate");
    } catch (e) {
        toast(e.message, true);
    } finally {
        btn.disabled = false;
    }
});
$("#eval-sha").addEventListener("change", syncEvalShaCustomInput);
