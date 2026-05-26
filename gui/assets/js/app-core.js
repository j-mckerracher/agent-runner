/*
 * Shared GUI state, API helpers, repository pickers, formatting helpers, and
 * workflow rail rendering. Keep cross-view utilities here; put view-specific
 * behavior in the dedicated files loaded after this one.
 */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
let RUNNER_MODELS = {
    claude: [],
    codex: [
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.3-codex",
        "gpt-5.2",
    ],
    copilot: [],
    gemini: [],
    "openai-compat": [],
};
let REPO_PATH_OPTIONS = [];
let REPO_CUSTOM_VALUES = [];
let REPO_BASE_DIR = "";
let ACTIVE_STORY_SOURCE = "manual";
let AZURE_DEVOPS_STATUS = null;
let EVAL_BOOTSTRAP_SHA = "";
const EVAL_CUSTOM_SHA_OPTION = "__custom__";
const REPO_PICKERS = [
    {
        input: "#f-repo",
        toggle: "#f-repo-toggle",
        menu: "#f-repo-menu",
    },
    {
        input: "#eval-repo",
        toggle: "#eval-repo-toggle",
        menu: "#eval-repo-menu",
    },
];
let activeJobId = null;
let activeAgentName = null;
let activeStream = null;
let activeStageGroup = null;
let activeRunJob = null;
let activeRunEvents = [];
let workflowTicker = null;
let lastSeq = 0;
let submitRunInFlight = false;
const RUN_HISTORY_STATE = {
    items: [],
    status: "all",
    range: "all",
    runner: "all",
    collapsed: false,
};
const ACTIVE_JOB_BY_SURFACE = { runs: null, evaluate: null };
const TRACE_EVENTS = { runs: [], evaluate: [] };
const STREAM_EVENT_TYPES = [
    "job.start",
    "stage.start",
    "stage.end",
    "cli.invoke",
    "cli.exit",
    "log",
    "metrics",
    "opik.start",
    "opik.end",
    "job.end",
    "stream.end",
    "user.prompt",
    "user.response",
    "user.prompt.timeout",
    "user.escalation.resolved",
];
const TELEMETRY_STATE = {
    loaded: false,
    loading: false,
    selectionMode: "all",
    selectedIds: new Set(),
    visibleRuns: [],
    aggregate: null,
    lastRefresh: null,
    timer: null,
    detailJobId: null,
    failedStage: null,
    missingEventsOnly: false,
    autoSelected: true,
    charts: {},
    chartBucket: "day",
    rollup: "run",
};
const TELEMETRY_MODEL_PALETTE = [
    "#4a9dd4",
    "#b48cff",
    "#52c97b",
    "#f0cc5e",
    "#ff8c66",
    "#64d8cb",
    "#f472b6",
    "#a3e635",
    "#fb7185",
    "#93c5fd",
];
function telemetryStableHash(value) {
    const text = String(value || "unknown");
    let hash = 0;
    for (let i = 0; i < text.length; i++)
        hash = ((hash << 5) - hash + text.charCodeAt(i)) | 0;
    return Math.abs(hash);
}
function telemetryColorForModel(model) {
    return TELEMETRY_MODEL_PALETTE[
        telemetryStableHash(model) % TELEMETRY_MODEL_PALETTE.length
    ];
}
const SETTINGS_HELP_COPY = {
    "s-host":
        "The network interface the API server binds to. Use 127.0.0.1 (default) to keep it local-only. Set to 0.0.0.0 if you need to access the GUI from another machine on your network.",
    "s-port":
        "TCP port for the API server and GUI. Default is 8742. If another service uses this port, change it here — but you must restart the server for the new port to take effect.",
    "s-runner":
        "Which AI backend to use by default when submitting new runs. Claude, Codex, Copilot, and Gemini are built-in; any configured aliases also appear here.",
    "s-model":
        "The specific model variant for the selected runner. For example: claude-sonnet-4-20250514 for Claude, gpt-5.2-codex for Codex, or gpt-4o for a Copilot alias. Options update when you change the runner.",
    "s-mode":
        "Live mode makes real LLM calls and costs real tokens. Hermetic mode records or replays cassette files for deterministic testing without API costs.",
    "s-conc":
        "How many workflow runs can execute simultaneously. Each run spawns a subprocess, so set this based on your machine's resources and API rate limits. Default is 2.",
    "s-data":
        "Where the server stores jobs.db, cassettes, and memory files. Defaults to ~/.agent-runner/. Change this if you want project-specific isolation or a different disk location.",
    "s-server-restart-note":
        "Changing Host or Port updates the saved config file immediately, but the running server process continues listening on the old address. Restart the server to apply.",
    "settings-save":
        "Writes all values on this page to ~/.agent-runner/config.json. The server picks up most changes immediately; host/port require a restart.",
    "s-alias-add-btn":
        "Create a custom runner alias that wraps any LiteLLM-compatible provider. Aliases appear in all runner dropdowns alongside built-in options.",
    "s-alias-name":
        "A stable short name for this alias (e.g. 'azure-gpt4o'). This becomes the value stored in run records and shown in the UI, so keep it descriptive and don't change it later.",
    "s-alias-provider":
        "The LiteLLM provider prefix that determines how the model string and auth are constructed. Use 'openai-compat' to connect to any OpenAI API-compatible endpoint — including self-hosted models (LM Studio, Ollama), third-party gateways (OpenRouter, Together AI), DeepSeek, Mistral, or any LiteLLM proxy. It accepts any model name and reads your API key from the env var you specify. Use 'azure' for Azure OpenAI deployments, or 'anthropic' for direct Anthropic access.",
    "s-alias-model-name":
        "The model identifier passed to the provider (e.g. 'gpt-4o', 'claude-sonnet-4-20250514'). Combined with the provider to form the full model string.",
    "s-alias-api-key-env":
        "Name of an environment variable holding the API key (e.g. 'AZURE_OPENAI_API_KEY'). The server reads this var at runtime. Leave blank if auth is handled another way.",
    "s-alias-base-url":
        "Custom endpoint URL for this alias (e.g. 'https://my-org.openai.azure.com'). Required for Azure deployments or self-hosted gateways. Omit for standard provider endpoints.",
    "s-alias-api-version":
        "API version string required by some providers (e.g. '2024-02-01' for Azure OpenAI). Leave blank if the provider doesn't require a version parameter.",
    "s-alias-num-retries":
        "Number of automatic retries on transient failures (rate limits, timeouts). Higher values help with bursty providers but increase wait time on hard failures. Default: provider's built-in retry.",
    "s-alias-timeout":
        "Maximum seconds to wait for a single LLM response. Increase for slow providers or large-context requests (e.g. 600 for long code generation). Default: provider's built-in timeout.",
    "s-alias-cancel-btn":
        "Discard the current alias form without saving. No changes are persisted.",
    "s-alias-save-btn":
        "Save this alias to config. It immediately becomes available in all runner dropdowns across Runs, Evaluate, and Agent Model Defaults.",
    "s-repo-base-dir":
        "Root directory scanned for repo path suggestions. Immediate child folders (excluding hidden ones like .git) appear in the repo picker dropdown on Runs and Evaluate tabs.",
    "repo-custom-value": ({ value }) =>
        `Remembered custom repo path: ${value}. This path stays in the repo picker dropdown even if it's not under the configured base directory. Click Remove to forget it.`,
    "runner-alias-row": ({ name, providerModel }) =>
        `Runner alias "${name}" resolves to ${providerModel}. It appears in runner selectors alongside built-in runners, and can have per-agent model overrides below.`,
    "s-opik-url":
        "The base URL for your Opik dashboard (e.g. 'https://www.comet.com/opik' or your self-hosted instance). Used for building trace links and API calls.",
    "s-opik-connect":
        "Auto-detect workspace and project ID from the dashboard URL and project name. Fills in the fields below so you don't have to find the IDs manually.",
    "s-opik-workspace":
        "Your Opik workspace slug (visible in the dashboard URL after /workspaceGuard/). Required for building correct trace and evaluation deep links.",
    "s-opik-project-id":
        "The UUID of your Opik project. Found in the project settings or URL. Needed for API calls and deep links to the correct project.",
    "s-opik-project-name":
        "Human-readable project name in Opik (e.g. 'agent-runner'). Used by bootstrap and connect to find or create the project. Must match what's configured in Opik.",
    "s-opik-status":
        "Shows whether Opik is fully configured. All four fields (URL, workspace, project ID, project name) are required — workflow runs will fail fast if any are missing.",
    "agent-default-row": ({ agentName }) =>
        `Per-agent model overrides for "${agentName}". When set, this agent always uses the specified model instead of the runner's default. Useful for giving complex agents a more capable model.`,
    "agent-default-cell": ({ agentName, runner }) =>
        `Model override for "${agentName}" when using ${runner}. Select a specific model, or leave on (default) to inherit whatever model the runner is configured with.`,
};
let activeSettingsHelpTrigger = null;

function toast(msg, err = false, kind = "") {
    const t = $("#toast");
    t.textContent = msg;
    const semantic = err
        ? " error"
        : kind === "success"
          ? " success"
          : "";
    t.className = "toast show" + semantic;
    setTimeout(() => t.classList.remove("show"), 3000);
}
function resolveSettingsHelpText(helpKey, context = {}) {
    const value = SETTINGS_HELP_COPY[helpKey];
    if (typeof value === "function") return value(context || {});
    return value || "";
}
function createSettingsHelpTrigger(
    helpKey,
    context = {},
    ariaLabel = "More info",
) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "set-help-trigger";
    btn.dataset.helpKey = helpKey;
    btn.dataset.helpContext = JSON.stringify(context || {});
    const nativeText = resolveSettingsHelpText(helpKey, context);
    if (nativeText) btn.dataset.helpText = nativeText;
    btn.setAttribute("aria-label", ariaLabel);
    btn.textContent = "i";
    // Keep native title as a fallback only until the custom tooltip takes over.
    if (nativeText) btn.title = nativeText;
    return btn;
}
function parseSettingsHelpContext(trigger) {
    try {
        return JSON.parse(trigger.dataset.helpContext || "{}");
    } catch {
        return {};
    }
}
function positionSettingsHelpTooltip(trigger) {
    const tooltip = $("#settings-help-tooltip");
    if (!tooltip || tooltip.hidden) return;
    const rect = trigger.getBoundingClientRect();
    const pad = 12;
    // Force layout so offsetWidth/offsetHeight are available
    const width = tooltip.offsetWidth || 200;
    const height = tooltip.offsetHeight || 40;
    let left = rect.left + rect.width / 2 - width / 2;
    left = Math.max(
        pad,
        Math.min(left, window.innerWidth - width - pad),
    );
    let top = rect.bottom + 10;
    if (top + height > window.innerHeight - pad) {
        top = Math.max(pad, rect.top - height - 10);
    }
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
}
function showSettingsHelpTooltip(trigger) {
    const tooltip = $("#settings-help-tooltip");
    if (!tooltip) return;
    const text =
        trigger.dataset.helpText ||
        resolveSettingsHelpText(
            trigger.dataset.helpKey,
            parseSettingsHelpContext(trigger),
        );
    if (!text) return;
    if (
        activeSettingsHelpTrigger &&
        activeSettingsHelpTrigger !== trigger
    ) {
        activeSettingsHelpTrigger.removeAttribute(
            "aria-describedby",
        );
        // Restore native title on previous trigger
        const prevText =
            activeSettingsHelpTrigger.dataset.helpText ||
            resolveSettingsHelpText(
                activeSettingsHelpTrigger.dataset.helpKey,
                parseSettingsHelpContext(activeSettingsHelpTrigger),
            );
        if (prevText) activeSettingsHelpTrigger.title = prevText;
    }
    activeSettingsHelpTrigger = trigger;
    // Suppress native title while custom tooltip is active (avoids double tooltip)
    trigger.title = "";
    trigger.setAttribute(
        "aria-describedby",
        "settings-help-tooltip",
    );
    tooltip.textContent = text;
    tooltip.hidden = false;
    tooltip.style.display = "block";
    positionSettingsHelpTooltip(trigger);
}
function hideSettingsHelpTooltip() {
    const tooltip = $("#settings-help-tooltip");
    if (activeSettingsHelpTrigger) {
        activeSettingsHelpTrigger.removeAttribute(
            "aria-describedby",
        );
        // Restore native title
        const text =
            activeSettingsHelpTrigger.dataset.helpText ||
            resolveSettingsHelpText(
                activeSettingsHelpTrigger.dataset.helpKey,
                parseSettingsHelpContext(activeSettingsHelpTrigger),
            );
        if (text) activeSettingsHelpTrigger.title = text;
    }
    activeSettingsHelpTrigger = null;
    if (tooltip) {
        tooltip.hidden = true;
        tooltip.style.display = "none";
    }
}
function ensureSettingsFieldHead(labelEl) {
    if (labelEl.parentElement?.classList.contains("set-field-head"))
        return labelEl.parentElement;
    const wrap = document.createElement("div");
    wrap.className = "set-field-head";
    labelEl.parentNode.insertBefore(wrap, labelEl);
    wrap.appendChild(labelEl);
    return wrap;
}
function attachFieldLabelHelp(labelSelector, helpKey, ariaLabel) {
    const label = $(labelSelector);
    if (!label) return;
    const head = ensureSettingsFieldHead(label);
    if (
        head.querySelector(
            `.set-help-trigger[data-help-key="${helpKey}"]`,
        )
    )
        return;
    head.appendChild(
        createSettingsHelpTrigger(
            helpKey,
            {},
            ariaLabel || `Help: ${label.textContent.trim()}`,
        ),
    );
}
function attachInlineHelp(targetSelector, helpKey, ariaLabel) {
    const target = $(targetSelector);
    if (
        !target ||
        target.parentElement?.querySelector(
            `.set-help-trigger[data-help-key="${helpKey}"]`,
        )
    )
        return;
    const wrap = document.createElement("span");
    wrap.className = "set-help-inline";
    target.parentNode.insertBefore(wrap, target);
    wrap.appendChild(target);
    wrap.appendChild(
        createSettingsHelpTrigger(
            helpKey,
            {},
            ariaLabel ||
                `Help: ${target.textContent.trim() || target.id || helpKey}`,
        ),
    );
}
function attachSettingsStaticHelp() {
    attachFieldLabelHelp('label[for="s-host"]', "s-host");
    attachFieldLabelHelp('label[for="s-port"]', "s-port");
    attachFieldLabelHelp('label[for="s-runner"]', "s-runner");
    attachFieldLabelHelp('label[for="s-model"]', "s-model");
    attachFieldLabelHelp('label[for="s-mode"]', "s-mode");
    attachFieldLabelHelp('label[for="s-conc"]', "s-conc");
    attachFieldLabelHelp('label[for="s-data"]', "s-data");
    attachFieldLabelHelp(
        'label[for="s-alias-name"]',
        "s-alias-name",
    );
    attachFieldLabelHelp(
        'label[for="s-alias-provider"]',
        "s-alias-provider",
    );
    attachFieldLabelHelp(
        'label[for="s-alias-model-name"]',
        "s-alias-model-name",
    );
    attachFieldLabelHelp(
        'label[for="s-alias-api-key-env"]',
        "s-alias-api-key-env",
    );
    attachFieldLabelHelp(
        'label[for="s-alias-base-url"]',
        "s-alias-base-url",
    );
    attachFieldLabelHelp(
        'label[for="s-alias-api-version"]',
        "s-alias-api-version",
    );
    attachFieldLabelHelp(
        'label[for="s-alias-num-retries"]',
        "s-alias-num-retries",
    );
    attachFieldLabelHelp(
        'label[for="s-alias-timeout"]',
        "s-alias-timeout",
    );
    attachFieldLabelHelp(
        'label[for="s-repo-base-dir"]',
        "s-repo-base-dir",
    );
    attachFieldLabelHelp('label[for="s-opik-url"]', "s-opik-url");
    attachFieldLabelHelp(
        'label[for="s-opik-workspace"]',
        "s-opik-workspace",
    );
    attachFieldLabelHelp(
        'label[for="s-opik-project-id"]',
        "s-opik-project-id",
    );
    attachFieldLabelHelp(
        'label[for="s-opik-project-name"]',
        "s-opik-project-name",
    );
    attachInlineHelp(
        "#settings-save",
        "settings-save",
        "Help: Save settings",
    );
    attachInlineHelp(
        "#s-alias-add-btn",
        "s-alias-add-btn",
        "Help: Add runner alias",
    );
    attachInlineHelp(
        "#s-alias-cancel-btn",
        "s-alias-cancel-btn",
        "Help: Cancel alias edits",
    );
    attachInlineHelp(
        "#s-alias-save-btn",
        "s-alias-save-btn",
        "Help: Save alias",
    );
    attachInlineHelp(
        "#s-opik-connect",
        "s-opik-connect",
        "Help: Connect Opik",
    );
    attachInlineHelp(
        "#s-opik-status",
        "s-opik-status",
        "Help: Opik status",
    );
    attachInlineHelp(
        "#s-server-restart-note",
        "s-server-restart-note",
        "Help: Server restart note",
    );
}
function formatApiError(status, payload, fallbackText) {
    let message = "";
    if (payload && payload.detail != null) {
        if (typeof payload.detail === "string") {
            message = payload.detail;
        } else if (Array.isArray(payload.detail)) {
            message = payload.detail
                .map((item) => {
                    const loc = Array.isArray(item.loc)
                        ? item.loc
                              .filter((part) => part !== "body")
                              .join(".")
                        : "";
                    return loc ? `${loc}: ${item.msg}` : item.msg;
                })
                .join("; ");
        } else {
            message = JSON.stringify(payload.detail);
        }
    } else {
        message = (fallbackText || "").trim();
    }
    return message ? `${status} ${message}` : String(status);
}
async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) {
        const ct = r.headers.get("content-type") || "";
        let payload = null;
        let text = "";
        if (ct.includes("json")) {
            try {
                payload = await r.json();
            } catch {
                text = await r.text();
            }
        } else {
            text = await r.text();
        }
        throw new Error(formatApiError(r.status, payload, text));
    }
    return r.headers.get("content-type")?.includes("json")
        ? r.json()
        : r.text();
}
function uniqueSorted(values) {
    return [
        ...new Set(
            values
                .map((v) => (typeof v === "string" ? v.trim() : ""))
                .filter(Boolean),
        ),
    ].sort();
}
function arrayValues(value) {
    return Array.isArray(value) ? value : [];
}
function allRepoPathValues() {
    return uniqueSorted([
        ...REPO_PATH_OPTIONS,
        ...REPO_CUSTOM_VALUES,
    ]);
}
function normalizeRepoPathForDisplay(value) {
    return (value || "").replace(/\\/g, "/");
}
function repoPathDisplayLabel(value) {
    const raw = (value || "").trim();
    if (!raw) return "";
    if (!REPO_PATH_OPTIONS.includes(raw)) return raw;
    const normalized = normalizeRepoPathForDisplay(raw);
    const normalizedBase = normalizeRepoPathForDisplay(
        REPO_BASE_DIR.trim(),
    ).replace(/\/+$/, "");
    if (normalizedBase) {
        const lowerPath = normalized.toLowerCase();
        const lowerBase = normalizedBase.toLowerCase();
        if (lowerPath === lowerBase) {
            return normalized.split("/").pop() || raw;
        }
        if (lowerPath.startsWith(`${lowerBase}/`)) {
            const relative = normalized.slice(
                normalizedBase.length + 1,
            );
            if (relative) return relative;
        }
    }
    return normalized.split("/").pop() || raw;
}
function matchingRepoPathValues(query) {
    const filter = (query || "").trim().toLowerCase();
    const values = allRepoPathValues();
    if (!filter) return values;
    return values.filter((value) => {
        const rawValue = value.toLowerCase();
        const labelValue =
            repoPathDisplayLabel(value).toLowerCase();
        return (
            rawValue.includes(filter) || labelValue.includes(filter)
        );
    });
}
function repoPickerElements(config) {
    const input = $(config.input);
    return {
        root: input ? input.closest(".repo-combobox") : null,
        input,
        toggle: $(config.toggle),
        menu: $(config.menu),
    };
}
function closeRepoPicker(config) {
    const { input, toggle, menu } = repoPickerElements(config);
    if (!input || !toggle || !menu) return;
    menu.hidden = true;
    input.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-expanded", "false");
}
function closeAllRepoPickers(exceptInputSelector = "") {
    REPO_PICKERS.forEach((config) => {
        if (config.input === exceptInputSelector) return;
        closeRepoPicker(config);
    });
}
function renderRepoPickerMenu(config) {
    const { input, menu } = repoPickerElements(config);
    if (!input || !menu) return;
    const values = matchingRepoPathValues(input.value);
    menu.innerHTML = "";
    if (values.length) {
        values.forEach((value) => {
            const option = document.createElement("button");
            option.type = "button";
            option.className = "repo-dropdown-option";
            option.setAttribute("role", "option");
            option.setAttribute(
                "aria-selected",
                String(input.value.trim() === value),
            );
            option.textContent = repoPathDisplayLabel(value);
            option.title = value;
            option.addEventListener("click", () => {
                input.value = value;
                updateRepoForgetButtons();
                closeRepoPicker(config);
                input.focus();
            });
            menu.appendChild(option);
        });
    } else {
        const empty = document.createElement("div");
        empty.className = "repo-dropdown-empty";
        empty.textContent = allRepoPathValues().length
            ? "No matching saved repos."
            : "No saved repo suggestions yet.";
        menu.appendChild(empty);
    }
    const hint = document.createElement("div");
    hint.className = "repo-dropdown-hint";
    hint.textContent =
        "Choose a saved repo below, or type any repo path.";
    menu.appendChild(hint);
}
function openRepoPicker(config) {
    const { input, toggle, menu } = repoPickerElements(config);
    if (!input || !toggle || !menu) return;
    closeAllRepoPickers(config.input);
    renderRepoPickerMenu(config);
    menu.hidden = false;
    input.setAttribute("aria-expanded", "true");
    toggle.setAttribute("aria-expanded", "true");
}
function renderRepoPathOptions() {
    REPO_PICKERS.forEach((config) => {
        const { menu } = repoPickerElements(config);
        if (menu && !menu.hidden) renderRepoPickerMenu(config);
    });
}
function setupRepoPicker(config) {
    const { root, input, toggle, menu } =
        repoPickerElements(config);
    if (!root || !input || !toggle || !menu) return;
    toggle.addEventListener("click", (event) => {
        event.preventDefault();
        if (menu.hidden) openRepoPicker(config);
        else closeRepoPicker(config);
        input.focus();
    });
    input.addEventListener("click", () => openRepoPicker(config));
    input.addEventListener("input", () => {
        updateRepoForgetButtons();
        openRepoPicker(config);
    });
    input.addEventListener("change", updateRepoForgetButtons);
    input.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown") {
            event.preventDefault();
            openRepoPicker(config);
        } else if (event.key === "Escape") {
            closeRepoPicker(config);
        }
    });
    root.addEventListener("focusout", (event) => {
        if (root.contains(event.relatedTarget)) return;
        closeRepoPicker(config);
    });
}
function updateRepoForgetButtons() {
    [
        ["#f-repo", "#f-repo-forget"],
        ["#eval-repo", "#eval-repo-forget"],
    ].forEach(([inputSelector, buttonSelector]) => {
        const input = $(inputSelector);
        const button = $(buttonSelector);
        if (!input || !button) return;
        const value = input.value.trim();
        button.style.display = REPO_CUSTOM_VALUES.includes(value)
            ? "inline-block"
            : "none";
    });
}
function renderRepoCustomValues() {
    const container = $("#s-repo-custom-values");
    if (!container) return;
    container.innerHTML = "";
    if (!REPO_CUSTOM_VALUES.length) {
        const empty = document.createElement("div");
        empty.className = "set-empty";
        empty.textContent = "No custom repo paths remembered yet.";
        container.appendChild(empty);
        updateRepoForgetButtons();
        return;
    }
    REPO_CUSTOM_VALUES.forEach((value) => {
        const row = document.createElement("div");
        row.className = "set-list-row";
        const valueWrap = document.createElement("div");
        valueWrap.className = "set-help-inline";
        valueWrap.style.flex = "1";
        const label = document.createElement("div");
        label.className = "set-list-value";
        label.title = value;
        label.textContent = value;
        valueWrap.appendChild(label);
        valueWrap.appendChild(
            createSettingsHelpTrigger(
                "repo-custom-value",
                { value },
                `Help: remembered repo path ${value}`,
            ),
        );
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "set-btn-connect";
        remove.textContent = "Remove";
        remove.addEventListener("click", async () => {
            try {
                await removeRepoCustomValue(value);
            } catch (e) {
                toast(e.message, true);
            }
        });
        row.appendChild(valueWrap);
        row.appendChild(remove);
        container.appendChild(row);
    });
    updateRepoForgetButtons();
}
async function saveRepoPathSettings(
    customValues,
    baseDir = REPO_BASE_DIR,
) {
    const nextValues = uniqueSorted(customValues);
    await api("/settings", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
            repo_paths: {
                base_dir: baseDir,
                custom_values: nextValues,
            },
        }),
    });
    await loadSettings();
}
async function rememberRepoPath(value) {
    const repo = (value || "").trim();
    if (!repo) return;
    const cfg = await api("/settings");
    const discovered = arrayValues(cfg.repo_path_options);
    const customValues = uniqueSorted(
        arrayValues(cfg.repo_paths?.custom_values),
    );
    if (discovered.includes(repo) || customValues.includes(repo))
        return;
    await saveRepoPathSettings(
        [...customValues, repo],
        cfg.repo_paths?.base_dir || "",
    );
}
async function removeRepoCustomValue(value) {
    const target = (value || "").trim();
    if (!target) return;
    await saveRepoPathSettings(
        REPO_CUSTOM_VALUES.filter((item) => item !== target),
    );
    toast("Repo path removed");
}
async function removeCurrentRepoCustomValue(inputSelector) {
    const input = $(inputSelector);
    if (!input) return;
    await removeRepoCustomValue(input.value.trim());
}
function buildOpikProjectUrl(opik, queryParams = {}) {
    const dashboardUrl = (opik?.dashboard_url || "")
        .trim()
        .replace(/\/+$/, "");
    if (!dashboardUrl) return "";
    const workspace = (opik?.workspace_name || "").trim();
    const projectId = (opik?.project_id || "").trim();
    if (!workspace || !projectId) return dashboardUrl;
    const query = new URLSearchParams(queryParams).toString();
    const path = `/workspaceGuard/${encodeURIComponent(workspace)}/projects/${encodeURIComponent(projectId)}`;
    return `${dashboardUrl}${path}${query ? `?${query}` : ""}`;
}
function runSurface(surface = "runs") {
    if (surface === "evaluate") {
        return {
            title: "#eval-term-title",
            term: "#eval-term",
            alert: "#eval-run-alert",
            alertTitle: "#eval-run-alert-title",
            alertMsg: "#eval-run-alert-msg",
            alertMeta: "#eval-run-alert-meta",
            opikLink: "#eval-run-opik-link",
            opikHint: "#eval-opik-hint",
            traceSummary: "#eval-trace-summary",
            traceSummaryMeta: "#eval-trace-summary-meta",
            traceTimeline: "#eval-trace-timeline",
            cancel: "#eval-cancel-btn",
            metrics: [
                "eval-m-ti",
                "eval-m-to",
                "eval-m-cost",
                "eval-m-stage",
                "eval-m-total",
            ],
            stage: "#eval-m-stage",
        };
    }
    return {
        title: "#term-title",
        term: "#term",
        alert: "#run-alert",
        alertTitle: "#run-alert-title",
        alertMsg: "#run-alert-msg",
        alertMeta: "#run-alert-meta",
        opikLink: "#opik-link",
        opikHint: "#opik-hint",
        traceSummary: "#trace-summary",
        traceSummaryMeta: "#trace-summary-meta",
        traceTimeline: "#trace-timeline",
        cancel: "#cancel-btn",
        metrics: ["m-ti", "m-to", "m-cost", "m-stage", "m-total"],
        stage: "#m-stage",
    };
}
function formatPercent(value) {
    let n = Number(value);
    if (!Number.isFinite(n)) return "—";
    if (n >= 0 && n <= 1) n *= 100;
    return `${Math.round(n)}%`;
}
function formatPct(value) {
    return formatPercent(value);
}
function formatMoney(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    if (n > 0 && n < 0.0001) return "<$0.0001";
    return `$${n.toFixed(4)}`;
}
function formatSeconds(value) {
    if (value == null || value === "") return "—";
    const n = Number(value);
    return Number.isFinite(n) ? `${Math.round(n)}s` : "—";
}
function formatDuration(value) {
    return formatSeconds(value);
}
function formatNumber(value) {
    if (value == null || value === "") return "—";
    const n = Number(value);
    return Number.isFinite(n)
        ? Math.round(n).toLocaleString()
        : "—";
}
function formatCompactNumber(value) {
    if (value == null || value === "") return "—";
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    const sign = n < 0 ? "-" : "";
    const abs = Math.abs(n);
    const units = [
        [1_000_000_000, "B"],
        [1_000_000, "M"],
        [1_000, "K"],
    ];
    for (const [scale, suffix] of units) {
        if (abs >= scale) {
            const scaled = abs / scale;
            const rounded = Number(
                scaled.toFixed(scaled >= 10 ? 0 : 1),
            );
            return `${sign}${rounded}${suffix}`;
        }
    }
    return `${Math.round(n)}`;
}
function setActiveSurfaceJob(surface = "runs", job = null) {
    const next = job ? { ...job } : null;
    ACTIVE_JOB_BY_SURFACE[surface] = next;
    if (surface === "runs") activeRunJob = next;
}
function getActiveSurfaceJob(surface = "runs") {
    return ACTIVE_JOB_BY_SURFACE[surface] || null;
}
function estimateDisplayedCostUsd(tokensIn, tokensOut) {
    const ti = Math.max(0, Number(tokensIn) || 0);
    const to = Math.max(0, Number(tokensOut) || 0);
    return (ti / 1_000_000) * 3.0 + (to / 1_000_000) * 15.0;
}
function resolveDisplayedCostUsd(job) {
    if (!job) return NaN;
    const explicit = Number(job.cost_usd);
    if (Number.isFinite(explicit) && explicit > 0) return explicit;
    return estimateDisplayedCostUsd(job.tokens_in, job.tokens_out);
}
function renderSurfaceMetrics(surface = "runs") {
    const ui = runSurface(surface);
    const job = getActiveSurfaceJob(surface) || {};
    const tokensIn = Math.max(0, Number(job.tokens_in) || 0);
    const tokensOut = Math.max(0, Number(job.tokens_out) || 0);
    $("#" + ui.metrics[0]).textContent = tokensIn;
    $("#" + ui.metrics[1]).textContent = tokensOut;
    $("#" + ui.metrics[2]).textContent = formatMoney(
        resolveDisplayedCostUsd(job),
    );
    $("#" + ui.metrics[4]).textContent = tokensIn + tokensOut;
}

const RUN_WORKFLOW_STAGES = [
    {
        key: "materialize",
        label: "Materialize",
        detail: "Sync agent prompts",
    },
    {
        key: "intake",
        label: "Intake",
        detail: "Normalize the story context",
    },
    {
        key: "task-generation",
        label: "Task generation",
        detail: "Draft the execution plan",
    },
    {
        key: "task-assignment",
        label: "Task assignment",
        detail: "Schedule the work batches",
    },
    {
        key: "execution",
        label: "Execution",
        detail: "Run the implementation loops",
    },
    {
        key: "qa",
        label: "QA",
        detail: "Validate against acceptance criteria",
    },
    {
        key: "lessons-optimizer",
        label: "Lessons optimizer",
        detail: "Capture follow-on learning",
    },
];
const RUN_ACTIVE_STATUSES = new Set([
    "queued",
    "running",
    "awaiting_input",
]);

function parseWorkflowTimestamp(ts) {
    const ms = Date.parse(ts || "");
    return Number.isFinite(ms) ? ms : null;
}
function formatWorkflowElapsed(totalMs) {
    const safeMs = Math.max(0, Math.floor(Number(totalMs) || 0));
    const milli = safeMs % 1000;
    const totalSeconds = Math.floor(safeMs / 1000);
    const seconds = totalSeconds % 60;
    const totalMinutes = Math.floor(totalSeconds / 60);
    const minutes = totalMinutes % 60;
    const hours = Math.floor(totalMinutes / 60);
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}:${String(milli).padStart(3, "0")}`;
}
function clearWorkflowTicker() {
    if (workflowTicker == null) return;
    clearInterval(workflowTicker);
    workflowTicker = null;
}
function refreshWorkflowTimers() {
    if (!activeRunJob) return clearWorkflowTicker();
    const stages = buildWorkflowStages(
        activeRunJob,
        activeRunEvents,
    );
    let hasActive = false;
    stages.forEach((stage) => {
        if (stage.state === "active") hasActive = true;
        const timer = $(`[data-workflow-timer="${stage.key}"]`);
        if (timer) timer.textContent = stage.timerText;
    });
    if (!hasActive) clearWorkflowTicker();
}
function syncWorkflowTicker(stages) {
    if (!stages.some((stage) => stage.state === "active")) {
        clearWorkflowTicker();
        return;
    }
    if (workflowTicker != null) return;
    workflowTicker = setInterval(refreshWorkflowTimers, 43);
}
function workflowStatusCopy(state) {
    if (state === "complete") return "Lit";
    if (state === "active") return "Live";
    if (state === "failed") return "Fault";
    return "Dim";
}
function workflowSummary(job, stages) {
    if (!job) return "Select a run to illuminate the pipeline.";
    const completeCount = stages.filter(
        (stage) => stage.state === "complete",
    ).length;
    const activeStage = stages.find(
        (stage) => stage.state === "active",
    );
    const failedStage = stages.find(
        (stage) => stage.state === "failed",
    );
    const runner = job.runner ? `${job.runner} \u00b7 ` : "";
    if (failedStage)
        return `${runner}${job.status} \u00b7 halted at ${failedStage.label.toLowerCase()}`;
    if (activeStage)
        return `${runner}${job.status} \u00b7 ${activeStage.label.toLowerCase()} is live`;
    return `${runner}${job.status} \u00b7 ${completeCount}/${stages.length} stages lit`;
}
function buildWorkflowStages(job, events = []) {
    const completed = new Set();
    const timing = new Map(
        RUN_WORKFLOW_STAGES.map((stage) => [
            stage.key,
            { startedAt: null, endedAt: null },
        ]),
    );
    let active = job?.current_stage || null;
    let failed = null;
    events.forEach((ev) => {
        const stageTiming = ev.stage ? timing.get(ev.stage) : null;
        const eventMs = parseWorkflowTimestamp(ev.ts);
        if (ev.type === "stage.start" && ev.stage) {
            active = ev.stage;
            if (stageTiming) {
                stageTiming.startedAt =
                    stageTiming.startedAt ?? eventMs;
                stageTiming.endedAt = null;
            }
        } else if (ev.type === "stage.end" && ev.stage) {
            if (ev.status === "ok") completed.add(ev.stage);
            else if (ev.status === "error") failed = ev.stage;
            if (stageTiming) {
                stageTiming.startedAt =
                    stageTiming.startedAt ?? eventMs;
                stageTiming.endedAt = eventMs;
            }
            if (active === ev.stage) active = null;
        } else if (ev.type === "job.end") {
            active = null;
        }
    });
    if (job?.status === "failed" && !failed)
        failed = getFailedStage(events);
    if (job && !RUN_ACTIVE_STATUSES.has(job.status)) active = null;
    const now = Date.now();
    return RUN_WORKFLOW_STAGES.map((stage, index) => {
        const stageTiming = timing.get(stage.key) || {
            startedAt: null,
            endedAt: null,
        };
        let state = "pending";
        if (completed.has(stage.key)) state = "complete";
        else if (failed === stage.key) state = "failed";
        else if (active === stage.key) state = "active";
        let elapsedMs = 0;
        if (
            stageTiming.startedAt != null &&
            stageTiming.endedAt != null
        ) {
            elapsedMs = Math.max(
                0,
                stageTiming.endedAt - stageTiming.startedAt,
            );
        } else if (
            stageTiming.startedAt != null &&
            state === "active"
        ) {
            elapsedMs = Math.max(0, now - stageTiming.startedAt);
        }
        return {
            ...stage,
            state,
            index: index + 1,
            startedAt: stageTiming.startedAt,
            endedAt: stageTiming.endedAt,
            elapsedMs,
            timerText: formatWorkflowElapsed(elapsedMs),
        };
    });
}
function renderRunWorkflow(job = null, events = []) {
    const rail = $("#workflow-rail");
    const title = $("#workflow-title");
    const subtitle = $("#workflow-subtitle");
    const list = $("#workflow-stage-list");
    if (!rail || !title || !subtitle || !list) return;
    const stages = buildWorkflowStages(job, events);
    rail.classList.toggle("is-empty", !job);
    title.textContent = job
        ? job.change_id || job.id || "Selected run"
        : "Workflow rail";
    subtitle.textContent = workflowSummary(job, stages);
    list.innerHTML = "";
    stages.forEach((stage) => {
        const item = document.createElement("div");
        item.className = `workflow-stage is-${stage.state}`;
        item.innerHTML = `
      <div class="workflow-stage-index">${String(stage.index).padStart(2, "0")}</div>
      <div class="workflow-stage-rail">
        <span class="workflow-stage-node"></span>
        <span class="workflow-stage-line"></span>
      </div>
      <div class="workflow-stage-copy">
        <span class="workflow-stage-name">${escapeHtml(stage.label)}</span>
        <span class="workflow-stage-detail">${escapeHtml(stage.detail)}</span>
        <div class="workflow-stage-meta">
          <span class="workflow-stage-state">${escapeHtml(workflowStatusCopy(stage.state))}</span>
          <span class="workflow-stage-timer" data-workflow-timer="${escapeHtml(stage.key)}">${escapeHtml(stage.timerText)}</span>
        </div>
      </div>`;
        list.appendChild(item);
    });
    syncWorkflowTicker(stages);
}
function applyWorkflowEventToJob(job, ev) {
    if (!job) return job;
    const next = { ...job };
    if (ev.type === "stage.start" && ev.stage) {
        next.current_stage = ev.stage;
        next.status =
            next.status === "queued" ? "running" : next.status;
    } else if (
        ev.type === "stage.end" &&
        ev.stage &&
        next.current_stage === ev.stage
    ) {
        next.current_stage = null;
    } else if (ev.type === "user.prompt") {
        next.status = "awaiting_input";
    } else if (
        ev.type === "user.response" ||
        ev.type === "user.prompt.timeout"
    ) {
        next.status =
            Number(ev.pending_count_after || 0) > 0
                ? "awaiting_input"
                : "running";
    } else if (ev.type === "metrics") {
        next.tokens_in =
            (Number(next.tokens_in) || 0) +
            (Number(ev.tokens_in) || 0);
        next.tokens_out =
            (Number(next.tokens_out) || 0) +
            (Number(ev.tokens_out) || 0);
        next.cost_usd =
            (Number(next.cost_usd) || 0) +
            (Number(ev.cost_usd) || 0);
    } else if (ev.type === "job.end") {
        if (ev.status) next.status = ev.status;
        next.current_stage = null;
    }
    return next;
}
renderRunWorkflow();
