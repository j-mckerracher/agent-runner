/*
 * Application shell, navigation, health checks, and Settings view behavior.
 *
 * Settings is loaded early because it supplies runner/model choices used by the
 * Runs and Evaluations forms.
 */

// Navigation
$$("#nav .nav-item").forEach((item) => {
    item.addEventListener("click", () => {
        $$("#nav .nav-item").forEach((n) =>
            n.classList.remove("active"),
        );
        item.classList.add("active");
        const v = item.dataset.view;
        $$(".view").forEach((s) =>
            s.classList.toggle("active", s.dataset.view === v),
        );
        /* Show sidebar workflow rail only on Runs tab */
        const sidebarWF = $("#sidebar-workflow");
        if (sidebarWF)
            sidebarWF.classList.toggle("is-visible", v === "runs");
        syncTelemetryPolling();
        if (v === "agents") loadAgents();
        if (v === "corpus") loadCorpus();
        if (v === "evaluate") loadEvaluate();
        if (v === "telemetry") refreshTelemetry();
        if (v === "settings") loadSettings();
    });
});

// Health
async function pollHealth() {
    try {
        await api("/health");
        $("#health-dot").classList.remove("off");
        $("#health-text").textContent = "API :8742";
    } catch {
        $("#health-dot").classList.add("off");
        $("#health-text").textContent = "API offline";
    }
}
setInterval(pollHealth, 5000);
pollHealth();

// Settings (also populates model/effort dropdowns)
const WORKFLOW_STAGE_AGENTS = [
    "intake",
    "task-generator",
    "task-plan-evaluator",
    "task-assigner",
    "assignment-evaluator",
    "software-engineer-hyperagent",
    "implementation-evaluator",
    "qa-engineer",
    "qa-evaluator",
    "pr-reviewer",
];

const CODEX_MODEL_LABELS = {
    "gpt-5.5":
        "gpt-5.5 (current) - Frontier model for complex coding, research, and real-world work.",
    "gpt-5.4": "gpt-5.4 - Strong model for everyday coding.",
    "gpt-5.4-mini":
        "gpt-5.4-mini - Small, fast, and cost-efficient model for simpler coding tasks.",
    "gpt-5.3-codex": "gpt-5.3-codex - Coding-optimized model.",
    "gpt-5.2":
        "gpt-5.2 - Optimized for professional work and long-running agents.",
};

function modelOptionLabel(runner, model) {
    return runner === "codex"
        ? CODEX_MODEL_LABELS[model] || model
        : model;
}

function buildAgentDefaultsUI(agentDefaults) {
    const container = $("#agent-defaults-container");
    container.innerHTML = "";
    const runners = Object.keys(RUNNER_MODELS);

    const AGENT_TAGS = {
        intake: ["core", "tag-intake"],
        "task-generator": ["planner", "tag-generator"],
        "task-assigner": ["router", "tag-assigner"],
        "software-engineer-hyperagent": ["exec", "tag-engineer"],
        "qa-engineer": ["qa", "tag-qa"],
        "lessons-optimizer-hyperagent": ["learn", "tag-optimizer"],
        "task-plan-evaluator": ["eval", "tag-evaluator"],
        "assignment-evaluator": ["eval", "tag-evaluator"],
        "implementation-evaluator": ["eval", "tag-evaluator"],
        "qa-evaluator": ["eval", "tag-qa"],
        "pr-reviewer": ["review", "tag-evaluator"],
    };

    const table = document.createElement("table");
    table.className = "set-agent-table";

    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    const agentTh = document.createElement("th");
    agentTh.textContent = "Agent";
    agentTh.style.width = "44%";
    headRow.appendChild(agentTh);
    runners.forEach((runner) => {
        const th = document.createElement("th");
        th.textContent = runner;
        headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    WORKFLOW_STAGE_AGENTS.forEach((agentName) => {
        const tr = document.createElement("tr");

        const nameTd = document.createElement("td");
        const nameWrap = document.createElement("div");
        nameWrap.className = "set-agent-name";
        const dot = document.createElement("span");
        dot.className = "set-agent-dot";
        nameWrap.appendChild(dot);
        nameWrap.appendChild(document.createTextNode(agentName));
        const tagInfo = AGENT_TAGS[agentName];
        if (tagInfo) {
            const tag = document.createElement("span");
            tag.className = `set-agent-tag ${tagInfo[1]}`;
            tag.textContent = tagInfo[0];
            nameWrap.appendChild(tag);
        }
        nameWrap.appendChild(
            createSettingsHelpTrigger(
                "agent-default-row",
                { agentName },
                `Help: agent override for ${agentName}`,
            ),
        );
        nameTd.appendChild(nameWrap);
        tr.appendChild(nameTd);

        runners.forEach((runner) => {
            const td = document.createElement("td");
            const select = document.createElement("select");
            select.id =
                `agent-model-${agentName}-${runner}`.replace(
                    /\./g,
                    "_",
                );

            const emptyOpt = document.createElement("option");
            emptyOpt.value = "";
            emptyOpt.textContent = "(default)";
            select.appendChild(emptyOpt);

            const models = RUNNER_MODELS[runner] || [];
            models.forEach((model) => {
                const opt = document.createElement("option");
                opt.value = model;
                opt.textContent = modelOptionLabel(runner, model);
                select.appendChild(opt);
            });

            const currentDefault =
                agentDefaults?.[agentName]?.[runner] || "";
            select.value = currentDefault;

            const selectWrap = document.createElement("div");
            selectWrap.className = "set-select-help-wrap";
            selectWrap.appendChild(select);
            selectWrap.appendChild(
                createSettingsHelpTrigger(
                    "agent-default-cell",
                    { agentName, runner },
                    `Help: model override for ${agentName} on ${runner}`,
                ),
            );

            if (runnerUsesFreeFormModel(runner)) {
                // Replace select with text input + datalist for free-form model runners.
                const input = document.createElement("input");
                input.type = "text";
                input.id = select.id;
                input.setAttribute(
                    "list",
                    modelDatalistForRunner(runner),
                );
                input.placeholder = "Enter model name";
                input.className = select.className;
                input.value = currentDefault;
                selectWrap.replaceChild(
                    input,
                    selectWrap.firstChild,
                );
            }

            td.appendChild(selectWrap);
            tr.appendChild(td);
        });

        tbody.appendChild(tr);
    });
    table.appendChild(tbody);

    container.appendChild(table);
}

function syncRunnerSelectFor(runnerSelectorId, modelSelectorId) {
    const sel = document.getElementById(runnerSelectorId);
    const currentVal = sel.value;
    sel.innerHTML = "";
    Object.keys(RUNNER_MODELS).forEach((runner) => {
        const o = document.createElement("option");
        o.value = runner;
        // Pretty label: alias runners show the alias model in parentheses
        const parts = runner.split("-");
        if (parts[0] === "copilot" && parts.length > 1) {
            o.textContent = `copilot (${parts.slice(1).join("-")})`;
        } else {
            o.textContent =
                runner.charAt(0).toUpperCase() + runner.slice(1);
        }
        sel.appendChild(o);
    });
    // Restore previous selection if still available, else first option
    if ([...sel.options].some((o) => o.value === currentVal)) {
        sel.value = currentVal;
    }
    if (modelSelectorId)
        syncModelSelectFor(
            "#" + runnerSelectorId,
            "#" + modelSelectorId,
        );
}

function runOverrideSafeId(agentName) {
    return agentName.replace(/[^a-zA-Z0-9_-]/g, "_");
}

const RUN_AGENT_OVERRIDES_STORAGE_KEY = "agent-runner.runAgentOverrides";

function loadRunAgentOverrideSelections() {
    try {
        const raw = window.localStorage?.getItem(
            RUN_AGENT_OVERRIDES_STORAGE_KEY,
        );
        if (!raw) return {};
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed.overrides === "object"
            ? parsed.overrides
            : {};
    } catch {
        return {};
    }
}

function persistRunAgentOverrideSelections() {
    const overrides = {};
    WORKFLOW_STAGE_AGENTS.forEach((agentName) => {
        const safe = runOverrideSafeId(agentName);
        overrides[agentName] = {
            runner: $(`#f-agent-runner-${safe}`)?.value || "",
            model: $(`#f-agent-model-${safe}`)?.value?.trim() || "",
        };
    });
    try {
        window.localStorage?.setItem(
            RUN_AGENT_OVERRIDES_STORAGE_KEY,
            JSON.stringify({ version: 1, overrides }),
        );
    } catch {
        // Ignore storage failures; the form remains usable without persistence.
    }
}

function syncRunAgentOverrideModel(agentName) {
    const safe = runOverrideSafeId(agentName);
    const runnerEl = $(`#f-agent-runner-${safe}`);
    const modelEl = $(`#f-agent-model-${safe}`);
    if (!runnerEl || !modelEl) return;
    const selectedRunner = runnerEl.value || $("#f-runner")?.value || "claude";
    const currentValue = modelEl.value || "";
    const parent = modelEl.parentNode;

    if (runnerUsesFreeFormModel(selectedRunner)) {
        let input = modelEl;
        if (modelEl.tagName !== "INPUT") {
            input = document.createElement("input");
            input.id = modelEl.id;
            input.className = modelEl.className;
            input.type = "text";
            parent.replaceChild(input, modelEl);
        }
        input.setAttribute("list", modelDatalistForRunner(selectedRunner));
        input.placeholder = "Use default model";
        input.value = currentValue;
        return;
    }

    let select = modelEl;
    if (modelEl.tagName !== "SELECT") {
        select = document.createElement("select");
        select.id = modelEl.id;
        select.className = modelEl.className;
        parent.replaceChild(select, modelEl);
    }
    select.innerHTML = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "(default)";
    select.appendChild(empty);
    (RUNNER_MODELS[selectedRunner] || []).forEach((model) => {
        const option = document.createElement("option");
        option.value = model;
        option.textContent = modelOptionLabel(selectedRunner, model);
        select.appendChild(option);
    });
    if ([...select.options].some((option) => option.value === currentValue)) {
        select.value = currentValue;
    }
}

function buildRunAgentOverridesUI() {
    const container = $("#f-agent-overrides");
    if (!container) return;
    container.innerHTML = "";
    const savedSelections = loadRunAgentOverrideSelections();
    WORKFLOW_STAGE_AGENTS.forEach((agentName) => {
        const safe = runOverrideSafeId(agentName);
        const row = document.createElement("div");
        row.className = "agent-override-row";

        const label = document.createElement("div");
        label.className = "agent-override-name";
        label.textContent = agentName;

        const runnerSelect = document.createElement("select");
        runnerSelect.id = `f-agent-runner-${safe}`;
        const defaultRunner = document.createElement("option");
        defaultRunner.value = "";
        defaultRunner.textContent = "default";
        runnerSelect.appendChild(defaultRunner);
        Object.keys(RUNNER_MODELS).forEach((runner) => {
            const option = document.createElement("option");
            option.value = runner;
            option.textContent =
                runner.charAt(0).toUpperCase() + runner.slice(1);
            runnerSelect.appendChild(option);
        });
        runnerSelect.addEventListener("change", () =>
            syncRunAgentOverrideModel(agentName),
        );

        const modelWrap = document.createElement("div");
        modelWrap.className = "agent-override-model";
        const modelSelect = document.createElement("select");
        modelSelect.id = `f-agent-model-${safe}`;
        modelWrap.appendChild(modelSelect);

        row.append(label, runnerSelect, modelWrap);
        container.appendChild(row);
        const saved = savedSelections[agentName] || {};
        if (
            saved.runner &&
            [...runnerSelect.options].some(
                (option) => option.value === saved.runner,
            )
        ) {
            runnerSelect.value = saved.runner;
        }
        syncRunAgentOverrideModel(agentName);
        const modelEl = $(`#f-agent-model-${safe}`);
        if (modelEl && saved.model) {
            if (
                modelEl.tagName === "INPUT" ||
                [...modelEl.options].some(
                    (option) => option.value === saved.model,
                )
            ) {
                modelEl.value = saved.model;
            }
        }
    });
    if (!container.dataset.persistenceBound) {
        container.addEventListener("input", (event) => {
            if (event.target?.id?.startsWith("f-agent-model-")) {
                persistRunAgentOverrideSelections();
            }
        });
        container.addEventListener("change", (event) => {
            if (event.target?.id?.startsWith("f-agent-")) {
                persistRunAgentOverrideSelections();
            }
        });
        container.dataset.persistenceBound = "true";
    }
}

function refreshRunAgentOverrideModelsUsingDefaultRunner() {
    WORKFLOW_STAGE_AGENTS.forEach((agentName) => {
        const safe = runOverrideSafeId(agentName);
        const runnerEl = $(`#f-agent-runner-${safe}`);
        if (runnerEl && !runnerEl.value) {
            syncRunAgentOverrideModel(agentName);
        }
    });
}

function collectRunAgentOverrides() {
    const overrides = {};
    WORKFLOW_STAGE_AGENTS.forEach((agentName) => {
        const safe = runOverrideSafeId(agentName);
        const runner = $(`#f-agent-runner-${safe}`)?.value || "";
        const model = $(`#f-agent-model-${safe}`)?.value?.trim() || "";
        if (!runner && !model) return;
        overrides[agentName] = {};
        if (runner) overrides[agentName].runner = runner;
        if (model) overrides[agentName].model = model;
    });
    return overrides;
}

function runnerUsesFreeFormModel(runner) {
    return (
        runner === "codex" ||
        runner === "openai-compat" ||
        RUNNER_ALIASES?.[runner]?.provider === "openai-compat"
    );
}

function modelDatalistForRunner(runner) {
    return runner === "codex"
        ? "codex-model-suggestions"
        : "openai-compat-model-suggestions";
}

function updateOpikSettingsStatus(opik) {
    const status = $("#s-opik-status");
    if (!status) return;
    const missing = [];
    if (!(opik?.dashboard_url || "").trim())
        missing.push("dashboard URL");
    if (!(opik?.workspace_name || "").trim())
        missing.push("workspace");
    if (!(opik?.project_id || "").trim())
        missing.push("project ID");
    if (!(opik?.project_name || "").trim())
        missing.push("project name");
    status.textContent = missing.length
        ? `Opik is required for workflow runs. Missing: ${missing.join(", ")}.`
        : `Opik configured for ${opik.workspace_name}/${opik.project_name}. Workflow traces will fail fast if this endpoint cannot be reached.`;
}

const STORY_SOURCE_CHANGE_HELP = {
    manual: "Leave blank to auto-generate from the manual work item ID or story title. Set this explicitly to group related runs in telemetry.",
    ado: "Leave blank to infer the change ID from the Azure DevOps work item URL. Set this explicitly to group related runs in telemetry.",
    story_file:
        "Leave blank to use the fixture's change_id when present; otherwise provide one here to group related runs in telemetry.",
};
const STORY_SOURCE_SHARED_NOTE = {
    manual: "Manual paste selected. Fill in the story fields above; these run options apply after submission.",
    ado: "Azure DevOps selected. The work item URL is the story source; these run options apply after fetch.",
    story_file:
        "Local fixture selected. The JSON fixture is the story source; these run options apply after load.",
};
function setStorySource(next) {
    ACTIVE_STORY_SOURCE = next || "manual";
    $$(".story-source-tab").forEach((btn) => {
        const active =
            btn.dataset.storySource === ACTIVE_STORY_SOURCE;
        btn.classList.toggle("active", active);
        btn.setAttribute("aria-pressed", active ? "true" : "false");
        btn.setAttribute("aria-checked", active ? "true" : "false");
    });
    $$(".story-source-panel").forEach((panel) => {
        const active =
            panel.id === `story-source-${ACTIVE_STORY_SOURCE}`;
        panel.classList.toggle("active", active);
        panel.hidden = !active;
    });
    const help = $("#f-change-help");
    if (help)
        help.textContent =
            STORY_SOURCE_CHANGE_HELP[ACTIVE_STORY_SOURCE] ||
            STORY_SOURCE_CHANGE_HELP.manual;
    const shared = $("#shared-options-note");
    if (shared)
        shared.textContent =
            STORY_SOURCE_SHARED_NOTE[ACTIVE_STORY_SOURCE] ||
            "These settings apply to every story source.";
}

function formatAzureDevOpsCliStatus(status) {
    if (!status?.detected) return "Status: Not detected";
    if (!status.extension_installed)
        return status.enabled
            ? "Status: Azure CLI detected, extension missing, enabled in settings"
            : "Status: Azure CLI detected, extension missing";
    return status.enabled
        ? "Status: Detected and enabled"
        : "Status: Detected but disabled in settings";
}

function formatAzureDevOpsMcpStatus(status) {
    if (!status?.configured)
        return status?.enabled
            ? "Status: Enabled, but no server URL configured"
            : "Status: Not configured";
    return status.enabled
        ? "Status: Configured and enabled"
        : "Status: Configured but disabled in settings";
}

function updateAzureDevOpsStatus(status) {
    AZURE_DEVOPS_STATUS = status || null;
    const adoStatus = $("#f-ado-status");
    const manualStatus = $("#s-ado-manual-status");
    const cliStatus = $("#s-ado-cli-status");
    const mcpStatus = $("#s-ado-mcp-status");
    const writebackStatus = $("#s-ado-writeback-status");
    if (manualStatus)
        manualStatus.textContent = "Status: Always available";
    if (!status) {
        if (adoStatus)
            adoStatus.innerHTML =
                "<strong>Manual story entry is available.</strong> Azure DevOps integration status could not be loaded.";
        if (cliStatus)
            cliStatus.textContent = "Status: Unavailable";
        if (mcpStatus)
            mcpStatus.textContent = "Status: Unavailable";
        if (writebackStatus)
            writebackStatus.textContent = "Status: Unknown";
        return;
    }
    const hasReadConnector = Boolean(
        status.cli?.can_read || status.mcp?.can_read,
    );
    if (adoStatus) {
        adoStatus.innerHTML = hasReadConnector
            ? "<strong>Azure DevOps integration is configured.</strong> You can still switch back to manual story entry at any time."
            : "<strong>Azure DevOps integration is not configured.</strong> You can still use Agent Workbench manually by pasting the story title, description, and acceptance criteria.";
    }
    if (cliStatus)
        cliStatus.textContent = formatAzureDevOpsCliStatus(
            status.cli,
        );
    if (mcpStatus)
        mcpStatus.textContent = formatAzureDevOpsMcpStatus(
            status.mcp,
        );
    if (writebackStatus)
        writebackStatus.textContent = status.write_back_enabled
            ? "Status: Enabled explicitly"
            : "Status: Disabled";
}

async function loadAzureDevOpsStatus() {
    try {
        const status = await api(
            "/integrations/azure-devops/status",
        );
        updateAzureDevOpsStatus(status);
    } catch (e) {
        updateAzureDevOpsStatus(null);
    }
}

async function loadSettings() {
    const cfg = await api("/settings");
    RUNNER_MODELS = cfg.runner_models || RUNNER_MODELS;
    // Populate free-form runner datalists with preset suggestions.
    [
        ["codex-model-suggestions", "codex"],
        ["openai-compat-model-suggestions", "openai-compat"],
    ].forEach(([datalistId, runner]) => {
        const datalist = document.getElementById(datalistId);
        if (!datalist) return;
        datalist.innerHTML = "";
        (RUNNER_MODELS[runner] || []).forEach((m) => {
            const o = document.createElement("option");
            o.value = m;
            datalist.appendChild(o);
        });
    });
    REPO_PATH_OPTIONS = arrayValues(cfg.repo_path_options);
    REPO_CUSTOM_VALUES = uniqueSorted(
        arrayValues(cfg.repo_paths?.custom_values),
    );
    REPO_BASE_DIR = cfg.repo_paths?.base_dir || "";
    $("#s-repo-base-dir").value = REPO_BASE_DIR;
    renderRepoPathOptions();
    renderRepoCustomValues();
    // Rebuild runner dropdowns to include any discovered copilot aliases
    syncRunnerSelectFor("f-runner", "f-model");
    syncRunnerSelectFor("eval-runner", "eval-model");
    syncRunnerSelectFor("s-runner", "s-model");
    // Rebuild the default-runner settings select
    const sRunnerSel = $("#s-runner");
    const savedRunner = cfg.defaults?.runner || "claude";
    if (
        [...sRunnerSel.options].some((o) => o.value === savedRunner)
    ) {
        sRunnerSel.value = savedRunner;
    }
    // Sync default model dropdown after runner is set
    syncModelSelectFor("#s-runner", "#s-model");
    const savedModel = cfg.defaults?.model || "";
    const sModelSel = $("#s-model");
    if (
        savedModel &&
        [...sModelSel.options].some((o) => o.value === savedModel)
    ) {
        sModelSel.value = savedModel;
    }
    $("#s-host").value = cfg.api?.host || "";
    $("#s-port").value = cfg.api?.port || "";
    $("#s-mode").value = cfg.defaults?.mode || "live";
    $("#s-conc").value = cfg.concurrency?.max_running_jobs || 2;
    $("#s-data").value = cfg.paths?.data_dir || "";
    $("#s-opik-url").value = cfg.opik?.dashboard_url || "";
    $("#s-opik-workspace").value = cfg.opik?.workspace_name || "";
    $("#s-opik-project-id").value = cfg.opik?.project_id || "";
    $("#s-opik-project-name").value = cfg.opik?.project_name || "";
    updateOpikSettingsStatus(cfg.opik || {});
    $("#s-ado-cli-enabled").value = String(
        Boolean(cfg.azure_devops?.cli?.enabled),
    );
    $("#s-ado-mcp-enabled").value = String(
        Boolean(cfg.azure_devops?.mcp?.enabled),
    );
    $("#s-ado-mcp-url").value =
        cfg.azure_devops?.mcp?.server_url || "";
    $("#s-ado-writeback-enabled").value = String(
        Boolean(cfg.azure_devops?.write_back_enabled),
    );
    await loadAzureDevOpsStatus();
    renderRunnerAliases(cfg.runner_aliases || {});
    buildAgentDefaultsUI(cfg.agent_model_defaults || {});
    buildRunAgentOverridesUI();
    syncRunConfigSelects();
}
// Runner alias state
let RUNNER_ALIASES = {};
function renderRunnerAliases(aliases) {
    RUNNER_ALIASES = aliases || {};
    const container = $("#s-alias-list");
    if (!container) return;
    container.innerHTML = "";
    const names = Object.keys(RUNNER_ALIASES);
    if (!names.length) {
        const empty = document.createElement("div");
        empty.className = "set-empty";
        empty.textContent = "No runner aliases configured.";
        container.appendChild(empty);
        return;
    }
    names.forEach((name) => {
        const alias = RUNNER_ALIASES[name];
        const row = document.createElement("div");
        row.className = "set-list-row";
        const labelWrap = document.createElement("div");
        labelWrap.className = "set-help-inline";
        labelWrap.style.flex = "1";
        const label = document.createElement("div");
        label.className = "set-list-value";
        label.style.flex = "1";
        const providerModel = [alias.provider, alias.model]
            .filter(Boolean)
            .join("/");
        const extras = [];
        if (alias.api_key_env)
            extras.push(`key: $${alias.api_key_env}`);
        if (alias.base_url) extras.push(`url: ${alias.base_url}`);
        label.textContent = `${name} → ${providerModel}${extras.length ? " · " + extras.join(" · ") : ""}`;
        label.title = JSON.stringify(alias, null, 2);
        labelWrap.appendChild(label);
        labelWrap.appendChild(
            createSettingsHelpTrigger(
                "runner-alias-row",
                { name, providerModel },
                `Help: runner alias ${name}`,
            ),
        );
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "set-btn-connect";
        remove.textContent = "Remove";
        remove.addEventListener("click", async () => {
            if (!confirm(`Remove runner alias "${name}"?`)) return;
            const next = { ...RUNNER_ALIASES };
            delete next[name];
            try {
                await api("/settings", {
                    method: "PUT",
                    headers: { "content-type": "application/json" },
                    body: JSON.stringify({ runner_aliases: next }),
                });
                toast(`Alias "${name}" removed`);
                await loadSettings();
            } catch (e) {
                toast(e.message, true);
            }
        });
        row.appendChild(labelWrap);
        row.appendChild(remove);
        container.appendChild(row);
    });
}
function clearAliasForm() {
    $("#s-alias-name").value = "";
    $("#s-alias-provider").value = "";
    $("#s-alias-model-name").value = "";
    $("#s-alias-api-key-env").value = "";
    $("#s-alias-base-url").value = "";
    $("#s-alias-api-version").value = "";
    $("#s-alias-num-retries").value = "";
    $("#s-alias-timeout").value = "";
    $("#s-alias-form-wrap").style.display = "none";
}
$("#s-alias-add-btn").addEventListener("click", () => {
    clearAliasForm();
    $("#s-alias-form-wrap").style.display = "";
    $("#s-alias-name").focus();
});
$("#s-alias-cancel-btn").addEventListener("click", clearAliasForm);
$("#s-alias-save-btn").addEventListener("click", async () => {
    const name = $("#s-alias-name").value.trim();
    const provider = $("#s-alias-provider").value.trim();
    const model = $("#s-alias-model-name").value.trim();
    if (!name || !provider || !model) {
        toast("Alias name, provider, and model are required", true);
        return;
    }
    const alias = { provider, model };
    const apiKeyEnv = $("#s-alias-api-key-env").value.trim();
    if (apiKeyEnv) alias.api_key_env = apiKeyEnv;
    const baseUrl = $("#s-alias-base-url").value.trim();
    if (baseUrl) alias.base_url = baseUrl;
    const apiVersion = $("#s-alias-api-version").value.trim();
    if (apiVersion) alias.api_version = apiVersion;
    const numRetries = $("#s-alias-num-retries").value.trim();
    if (numRetries) alias.num_retries = parseInt(numRetries, 10);
    const timeout = $("#s-alias-timeout").value.trim();
    if (timeout) alias.timeout = parseFloat(timeout);
    const next = { ...RUNNER_ALIASES, [name]: alias };
    try {
        await api("/settings", {
            method: "PUT",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ runner_aliases: next }),
        });
        toast(`Alias "${name}" saved`);
        clearAliasForm();
        await loadSettings();
    } catch (e) {
        toast(e.message, true);
    }
});
// Default runner → default model sync in settings
$("#s-runner").addEventListener("change", () =>
    syncModelSelectFor("#s-runner", "#s-model"),
);

$("#settings-save").addEventListener("click", async () => {
    try {
        const agentDefaults = {};
        WORKFLOW_STAGE_AGENTS.forEach((agentName) => {
            agentDefaults[agentName] = {};
            Object.keys(RUNNER_MODELS).forEach((runner) => {
                const select = $(
                    `#agent-model-${agentName}-${runner}`.replace(
                        /\./g,
                        "_",
                    ),
                );
                const value = select?.value || "";
                if (value) {
                    agentDefaults[agentName][runner] = value;
                }
            });
            if (
                Object.keys(agentDefaults[agentName]).length === 0
            ) {
                delete agentDefaults[agentName];
            }
        });

        const defaultModel = $("#s-model").value || null;
        await api("/settings", {
            method: "PUT",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
                api: {
                    host: $("#s-host").value,
                    port: parseInt($("#s-port").value, 10),
                },
                defaults: {
                    runner: $("#s-runner").value,
                    model: defaultModel,
                    mode: $("#s-mode").value,
                },
                concurrency: {
                    max_running_jobs: parseInt(
                        $("#s-conc").value,
                        10,
                    ),
                },
                paths: { data_dir: $("#s-data").value },
                repo_paths: {
                    base_dir: $("#s-repo-base-dir").value.trim(),
                    custom_values: REPO_CUSTOM_VALUES,
                },
                azure_devops: {
                    write_back_enabled:
                        $("#s-ado-writeback-enabled").value ===
                        "true",
                    cli: {
                        enabled:
                            $("#s-ado-cli-enabled").value ===
                            "true",
                    },
                    mcp: {
                        enabled:
                            $("#s-ado-mcp-enabled").value ===
                            "true",
                        server_url:
                            $("#s-ado-mcp-url").value.trim(),
                    },
                },
                opik: {
                    dashboard_url: $("#s-opik-url").value.trim(),
                    workspace_name:
                        $("#s-opik-workspace").value.trim(),
                    project_id:
                        $("#s-opik-project-id").value.trim(),
                    project_name: $(
                        "#s-opik-project-name",
                    ).value.trim(),
                },
                runner_aliases: RUNNER_ALIASES,
                agent_model_defaults: agentDefaults,
            }),
        });
        toast("Settings saved");
        await loadSettings();
    } catch (e) {
        toast(e.message, true);
    }
});
REPO_PICKERS.forEach(setupRepoPicker);
document.addEventListener("pointerdown", (event) => {
    if (event.target.closest(".repo-combobox")) return;
    closeAllRepoPickers();
});
$("#f-repo-forget").addEventListener("click", async () => {
    try {
        await removeCurrentRepoCustomValue("#f-repo");
    } catch (e) {
        toast(e.message, true);
    }
});
$("#eval-repo-forget").addEventListener("click", async () => {
    try {
        await removeCurrentRepoCustomValue("#eval-repo");
    } catch (e) {
        toast(e.message, true);
    }
});

$("#s-opik-connect").addEventListener("click", async () => {
    const btn = $("#s-opik-connect");
    const dashboardUrl = $("#s-opik-url").value.trim();
    const projectName =
        $("#s-opik-project-name").value.trim() || "agent-runner";
    if (!dashboardUrl) {
        toast("Enter an Opik dashboard URL first", true);
        return;
    }
    btn.textContent = "Connecting…";
    btn.disabled = true;
    try {
        const result = await api("/settings/opik/connect", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
                dashboard_url: dashboardUrl,
                project_name: projectName,
            }),
        });
        $("#s-opik-url").value =
            result.dashboard_url || dashboardUrl;
        $("#s-opik-workspace").value = result.workspace_name || "";
        $("#s-opik-project-id").value = result.project_id || "";
        $("#s-opik-project-name").value =
            result.project_name || projectName;
        updateOpikSettingsStatus(result);
        toast(
            "Opik connected — workspace and project ID filled in",
        );
    } catch (e) {
        toast(e.message, true);
    } finally {
        btn.textContent = "Connect";
        btn.disabled = false;
    }
});

function syncModelSelectFor(runnerSelector, modelSelector) {
    const r = $(runnerSelector).value;
    const existingEl = $(modelSelector);
    const parent = existingEl.parentNode;
    const currentValue = existingEl.value || "";

    if (runnerUsesFreeFormModel(r)) {
        if (existingEl.tagName !== "INPUT") {
            const input = document.createElement("input");
            input.type = "text";
            input.id = existingEl.id;
            input.setAttribute("list", modelDatalistForRunner(r));
            input.placeholder = "Enter model name";
            input.className = existingEl.className;
            input.value = currentValue;
            parent.replaceChild(input, existingEl);
        } else {
            existingEl.setAttribute(
                "list",
                modelDatalistForRunner(r),
            );
        }
        return;
    }

    // Closed runner: restore <select> if currently an <input>
    if (existingEl.tagName !== "SELECT") {
        const select = document.createElement("select");
        select.id = existingEl.id;
        select.className = existingEl.className;
        parent.replaceChild(select, existingEl);
    }

    const sel = $(modelSelector);
    sel.innerHTML = "";
    (RUNNER_MODELS[r] || []).forEach((m) => {
        const o = document.createElement("option");
        o.value = m;
        o.textContent = modelOptionLabel(r, m);
        sel.appendChild(o);
    });
    // Restore previous value if it matches a known option
    const selEl = $(modelSelector);
    if (
        currentValue &&
        [...selEl.options].some((o) => o.value === currentValue)
    ) {
        selEl.value = currentValue;
    }
}
function syncRunConfigSelects() {
    syncModelSelectFor("#f-runner", "#f-model");
    syncModelSelectFor("#eval-runner", "#eval-model");
}
$("#f-runner").addEventListener("change", () =>
    {
        syncModelSelectFor("#f-runner", "#f-model");
        refreshRunAgentOverrideModelsUsingDefaultRunner();
    },
);
$("#eval-runner").addEventListener("change", () =>
    syncModelSelectFor("#eval-runner", "#eval-model"),
);
document.addEventListener("pointerover", (event) => {
    const trigger = event.target.closest(".set-help-trigger");
    if (trigger) showSettingsHelpTooltip(trigger);
});
document.addEventListener("pointerout", (event) => {
    const trigger = event.target.closest(".set-help-trigger");
    if (!trigger || trigger !== activeSettingsHelpTrigger) return;
    if (
        event.relatedTarget?.closest?.(".set-help-trigger") ===
        trigger
    )
        return;
    hideSettingsHelpTooltip();
});
document.addEventListener("focusin", (event) => {
    const trigger = event.target.closest(".set-help-trigger");
    if (trigger) showSettingsHelpTooltip(trigger);
});
document.addEventListener("focusout", (event) => {
    if (event.target !== activeSettingsHelpTrigger) return;
    if (
        event.relatedTarget?.closest?.(".set-help-trigger") ===
        activeSettingsHelpTrigger
    )
        return;
    hideSettingsHelpTooltip();
});
document.addEventListener("click", (event) => {
    const trigger = event.target.closest(".set-help-trigger");
    if (trigger) {
        event.preventDefault();
        showSettingsHelpTooltip(trigger);
        return;
    }
    if (activeSettingsHelpTrigger) hideSettingsHelpTooltip();
});
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideSettingsHelpTooltip();
});
window.addEventListener("resize", () => {
    if (activeSettingsHelpTrigger)
        positionSettingsHelpTooltip(activeSettingsHelpTrigger);
});
$('.view[data-view="settings"] .simple-list')?.addEventListener(
    "scroll",
    () => {
        if (activeSettingsHelpTrigger)
            positionSettingsHelpTooltip(activeSettingsHelpTrigger);
    },
    { passive: true },
);
attachSettingsStaticHelp();
loadSettings();
