(function () {
    const state = {
        prefs: {},
        accounts: { claude: [], codex: [], gemini: [] },
        serverRunning: false,
        effortOptions: {},
    };

    const $ = (sel, root = document) => root.querySelector(sel);
    const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

    async function fetchJson(url, options = {}) {
        const response = await fetch(url, {
            headers: { "Content-Type": "application/json" },
            ...options,
        });
        if (!response.ok) {
            const text = await response.text().catch(() => "");
            throw new Error(`${response.status} ${response.statusText}: ${text}`);
        }
        if (response.status === 204) return null;
        return response.json();
    }

    function applyPrefs(prefs, effortOptions) {
        state.prefs = prefs || {};
        state.effortOptions = effortOptions || state.effortOptions;
        document.body.classList.toggle("oled", !!state.prefs.oled_theme);

        // Populate selects.
        for (const [key, options] of Object.entries(state.effortOptions)) {
            const select = document.querySelector(`select[data-pref="${key}"]`);
            if (!select) continue;
            const current = state.prefs[key];
            select.innerHTML = options
                .map(
                    (opt) =>
                        `<option value="${opt}"${opt === current ? " selected" : ""}>${opt}</option>`,
                )
                .join("");
        }

        // Populate inputs / checkboxes.
        $$("[data-pref]").forEach((input) => {
            const key = input.getAttribute("data-pref");
            if (!(key in state.prefs)) return;
            if (input.type === "checkbox") {
                input.checked = !!state.prefs[key];
            } else if (input.tagName === "SELECT") {
                input.value = state.prefs[key];
            } else {
                input.value = state.prefs[key] ?? "";
            }
        });

        // Provider toggles.
        for (const name of [
            "claude",
            "codex",
            "gemini",
            "synthetic",
            "kimi",
            "fireworks",
        ]) {
            const toggle = document.querySelector(`[data-provider-toggle="${name}"]`);
            if (toggle) {
                toggle.checked =
                    state.prefs.enabled_providers == null
                        ? true
                        : state.prefs.enabled_providers[name] !== false;
            }
        }

        // Max Budget warning visibility.
        const warn = document.getElementById("maxBudgetWarning");
        if (warn) warn.hidden = !state.prefs.claude_max_budget_mode;
    }

    function renderAccounts(accounts) {
        state.accounts = accounts || state.accounts;
        for (const service of ["claude", "codex", "gemini"]) {
            const list = document.querySelector(`[data-account-list="${service}"]`);
            if (!list) continue;
            const items = state.accounts[service] || [];
            if (items.length === 0) {
                list.innerHTML = '<li class="muted small">No connected accounts</li>';
                continue;
            }
            list.innerHTML = items
                .map((acc) => renderAccount(service, acc))
                .join("");
            list.querySelectorAll("[data-toggle]").forEach((btn) => {
                btn.addEventListener("click", () => toggleAccount(service, btn.dataset.id));
            });
            list.querySelectorAll("[data-remove]").forEach((btn) => {
                btn.addEventListener("click", () => removeAccount(service, btn.dataset.id));
            });
        }
    }

    function renderAccount(service, account) {
        const status = account.is_expired
            ? "expired"
            : account.disabled
              ? "disabled"
              : "active";
        return `<li>
            <div>
                <strong>${escapeHtml(account.display_name || account.id)}</strong>
                <span class="muted small"> (${status})</span>
            </div>
            <div>
                <button data-toggle data-id="${escapeHtml(account.id)}">${
                    account.disabled ? "Enable" : "Disable"
                }</button>
                <button data-remove data-id="${escapeHtml(account.id)}">Remove</button>
            </div>
        </li>`;
    }

    function escapeHtml(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function applyServerStatus(payload) {
        state.serverRunning = payload.server_running && payload.proxy_running;
        const dot = document.getElementById("statusDot");
        const txt = document.getElementById("statusText");
        const url = document.getElementById("proxyUrl");
        const btn = document.getElementById("btnToggleServer");
        dot.classList.toggle("dot-on", state.serverRunning);
        dot.classList.toggle("dot-off", !state.serverRunning);
        txt.textContent = state.serverRunning
            ? `Running on port ${payload.proxy_port}`
            : "Stopped";
        url.textContent = payload.proxy_url || "";
        btn.textContent = state.serverRunning ? "Stop" : "Start";

        applyFactoryStatus(payload.factory || {});
    }

    function applyFactoryStatus(factory) {
        const btn = document.getElementById("btnApplyFactoryModels");
        const status = document.getElementById("factoryModelsStatus");
        if (!btn || !status) return;
        const installed = !!factory.models_installed;
        btn.textContent = installed ? "Re-apply custom models" : "Apply custom models";
        btn.classList.toggle("primary", !installed);
        btn.classList.toggle("ghost", installed);
        status.textContent = installed
            ? `Applied to ${factory.settings_path || "~/.factory/settings.json"}`
            : "Not applied";
    }

    async function refresh() {
        try {
            const status = await fetchJson("/api/status");
            applyPrefs(status.prefs, status.effort_options);
            renderAccounts(status.accounts);
            applyServerStatus(status);
        } catch (err) {
            console.error("refresh failed", err);
        }
    }

    async function updatePref(key, value) {
        try {
            await fetchJson("/api/prefs", {
                method: "PATCH",
                body: JSON.stringify({ [key]: value }),
            });
        } catch (err) {
            alert(`Could not save ${key}: ${err.message}`);
        }
    }

    async function toggleProvider(name, enabled) {
        await fetchJson(`/api/prefs/providers/${name}`, {
            method: "POST",
            body: JSON.stringify({ enabled }),
        });
    }

    async function connectService(service) {
        const btn = document.querySelector(
            `[data-action="connect"][data-service="${service}"]`,
        );
        btn.disabled = true;
        btn.textContent = "Opening browser...";
        try {
            const result = await fetchJson(`/api/auth/${service}/login`, {
                method: "POST",
                body: "{}",
            });
            alert(result.message || "Complete the login in your browser.");
        } catch (err) {
            alert(`Login failed: ${err.message}`);
        } finally {
            btn.disabled = false;
            btn.textContent = "Connect";
        }
    }

    async function toggleAccount(service, id) {
        await fetchJson(`/api/auth/${service}/${encodeURIComponent(id)}/toggle`, {
            method: "POST",
            body: "{}",
        });
        const accounts = await fetchJson("/api/auth");
        renderAccounts(accounts);
    }

    async function removeAccount(service, id) {
        if (!confirm("Remove this account?")) return;
        await fetchJson(`/api/auth/${service}/${encodeURIComponent(id)}`, {
            method: "DELETE",
        });
        const accounts = await fetchJson("/api/auth");
        renderAccounts(accounts);
    }

    async function toggleServer() {
        const btn = document.getElementById("btnToggleServer");
        btn.disabled = true;
        try {
            const path = state.serverRunning ? "/api/server/stop" : "/api/server/start";
            const payload = await fetchJson(path, { method: "POST", body: "{}" });
            applyServerStatus(payload);
        } catch (err) {
            alert(`Server toggle failed: ${err.message}`);
        } finally {
            btn.disabled = false;
        }
    }

    function attachHandlers() {
        $$("[data-pref]").forEach((input) => {
            const key = input.getAttribute("data-pref");
            const eventName = input.type === "checkbox" ? "change" : "change";
            input.addEventListener(eventName, () => {
                const value =
                    input.type === "checkbox"
                        ? input.checked
                        : input.type === "number"
                          ? Number(input.value)
                          : input.value;
                updatePref(key, value);
                if (key === "oled_theme") document.body.classList.toggle("oled", !!value);
                if (key === "claude_max_budget_mode") {
                    const warn = document.getElementById("maxBudgetWarning");
                    if (warn) warn.hidden = !value;
                }
            });
        });

        $$("[data-provider-toggle]").forEach((input) => {
            input.addEventListener("change", () => {
                toggleProvider(input.getAttribute("data-provider-toggle"), input.checked);
            });
        });

        $$('[data-action="connect"]').forEach((btn) => {
            btn.addEventListener("click", () => connectService(btn.getAttribute("data-service")));
        });

        document
            .getElementById("btnToggleServer")
            .addEventListener("click", toggleServer);

        document.getElementById("btnCopyUrl").addEventListener("click", async () => {
            const url = document.getElementById("proxyUrl").textContent.trim();
            if (!url) return;
            try {
                await navigator.clipboard.writeText(url);
            } catch {
                prompt("Copy this URL", url);
            }
        });

        document.getElementById("btnTunnelStart").addEventListener("click", async () => {
            try {
                const res = await fetchJson("/api/tunnel/start", {
                    method: "POST",
                    body: "{}",
                });
                document.getElementById("tunnelUrl").textContent = res.url || "starting...";
            } catch (err) {
                alert(err.message);
            }
        });

        document.getElementById("btnTunnelStop").addEventListener("click", async () => {
            await fetchJson("/api/tunnel/stop", { method: "POST", body: "{}" });
            document.getElementById("tunnelUrl").textContent = "no tunnel";
        });

        document.getElementById("btnInstallDroids").addEventListener("click", async () => {
            try {
                const result = await fetchJson("/api/droids/install", {
                    method: "POST",
                    body: "{}",
                });
                const count =
                    (result.droids || []).length + (result.commands || []).length;
                document.getElementById("droidsInstallResult").textContent =
                    `Installed ${count} files into ~/.factory/`;
            } catch (err) {
                alert(err.message);
            }
        });

        document
            .getElementById("btnApplyFactoryModels")
            .addEventListener("click", async () => {
                const btn = document.getElementById("btnApplyFactoryModels");
                const status = document.getElementById("factoryModelsStatus");
                btn.disabled = true;
                try {
                    const result = await fetchJson("/api/factory/models/apply", {
                        method: "POST",
                        body: "{}",
                    });
                    const added = (result.installed || []).length;
                    const skipped = (result.skipped || []).length;
                    status.textContent =
                        `Applied ${added} model${added === 1 ? "" : "s"} to ${result.settings_path}` +
                        (skipped > 0 ? ` (${skipped} skipped via disabled provider)` : "");
                    await refresh();
                    alert(
                        `Added ${added} DroidProxy model${added === 1 ? "" : "s"} to Factory settings.\n\n` +
                            "Restart Factory (or open a new session) to see them in the model picker.",
                    );
                } catch (err) {
                    alert(`Could not apply custom models: ${err.message}`);
                } finally {
                    btn.disabled = false;
                }
            });
    }

    function openStreams() {
        const logs = new EventSource("/api/logs/stream");
        const logEl = document.getElementById("logTail");
        logs.onmessage = (event) => {
            try {
                const payload = JSON.parse(event.data);
                if (!payload.lines) return;
                logEl.textContent = payload.lines.join("\n");
                logEl.scrollTop = logEl.scrollHeight;
            } catch (err) {
                console.debug("log stream parse error", err);
            }
        };

        const auth = new EventSource("/api/auth/stream");
        auth.onmessage = (event) => {
            try {
                const payload = JSON.parse(event.data);
                renderAccounts(payload);
            } catch (err) {
                console.debug("auth stream parse error", err);
            }
        };
    }

    attachHandlers();
    refresh().then(openStreams);
    setInterval(refresh, 30000);
})();
