# DroidProxy Linux Setup

## 1. Launch DroidProxy

```bash
droidproxy            # tray + web UI (if GTK is available)
droidproxy daemon     # headless / SSH
```

Open <http://127.0.0.1:8316/> in your browser. This is the settings UI.

## 2. Authenticate

Click **Connect** next to Claude Code, Codex, or Gemini. A browser window
opens against the provider's OAuth flow. Tokens are written under
`~/.cli-proxy-api/` by the bundled Go binary and DroidProxy picks them up
automatically.

On headless systems (SSH, no `$DISPLAY`), the subprocess cannot launch a
browser -- copy the authentication URL from the DroidProxy log tail into a
browser on a machine with network access to your Linux host.

## 3. Configure Factory

Open `~/.factory/settings.json` and add the following to the
`customModels` array:

```json
"customModels": [
    {
      "model": "claude-opus-4-7",
      "id": "custom:droidproxy:opus-4-7",
      "index": 0,
      "baseUrl": "http://localhost:8317",
      "apiKey": "dummy-not-used",
      "displayName": "DroidProxy: Opus 4.7",
      "maxOutputTokens": 128000,
      "noImageSupport": false,
      "provider": "anthropic"
    },
    {
      "model": "claude-sonnet-4-6",
      "id": "custom:droidproxy:sonnet-4-6",
      "index": 1,
      "baseUrl": "http://localhost:8317",
      "apiKey": "dummy-not-used",
      "displayName": "DroidProxy: Sonnet 4.6",
      "maxOutputTokens": 64000,
      "noImageSupport": false,
      "provider": "anthropic"
    },
    {
      "model": "gpt-5.3-codex",
      "id": "custom:droidproxy:gpt-5.3-codex",
      "index": 2,
      "baseUrl": "http://localhost:8317/v1",
      "apiKey": "dummy-not-used",
      "displayName": "DroidProxy: GPT 5.3 Codex",
      "maxOutputTokens": 128000,
      "noImageSupport": false,
      "provider": "openai"
    },
    {
      "model": "gpt-5.5",
      "id": "custom:droidproxy:gpt-5.5",
      "index": 3,
      "baseUrl": "http://localhost:8317/v1",
      "apiKey": "dummy-not-used",
      "displayName": "DroidProxy: GPT 5.5",
      "maxOutputTokens": 128000,
      "noImageSupport": false,
      "provider": "openai"
    },
    {
      "model": "gemini-3.1-pro-preview",
      "id": "custom:droidproxy:gemini-3.1-pro",
      "index": 4,
      "baseUrl": "http://localhost:8317",
      "apiKey": "dummy-not-used",
      "displayName": "DroidProxy: Gemini 3.1 Pro",
      "maxOutputTokens": 65536,
      "noImageSupport": false,
      "provider": "google"
    },
    {
      "model": "gemini-3-flash-preview",
      "id": "custom:droidproxy:gemini-3-flash",
      "index": 5,
      "baseUrl": "http://localhost:8317",
      "apiKey": "dummy-not-used",
      "displayName": "DroidProxy: Gemini 3 Flash",
      "maxOutputTokens": 65536,
      "noImageSupport": false,
      "provider": "google"
    }
]
```

Claude entries use `provider: "anthropic"` with `http://localhost:8317`;
GPT / Codex / Gemini entries use `http://localhost:8317/v1`. DroidProxy
applies Claude adaptive thinking, Codex reasoning effort, and Gemini
thinking levels based on the selected model and the effort setting
configured in the web UI.

## 4. Configure thinking effort

1. Open the DroidProxy settings UI (`http://127.0.0.1:8316/`).
2. Set the desired effort per model:
   - Opus 4.7: `low`, `medium`, `high`, `xhigh`, or `max`
   - Sonnet 4.6: `low`, `medium`, `high`, or `max`
   - GPT 5.3 Codex: `low`, `medium`, `high`, or `xhigh`
   - GPT 5.5: `low`, `medium`, `high`, or `xhigh`
   - Gemini 3.1 Pro: `low`, `medium`, or `high`
   - Gemini 3 Flash: `minimal`, `low`, `medium`, or `high`

## 5. Max Budget Mode (optional)

Enables classic extended thinking for Sonnet 4.6 with `max_tokens=64000`
and `budget_tokens=63999`, bypassing the adaptive effort slider. Opus 4.7
is unaffected and keeps its configured effort. Expect to burn through
quota rapidly.

## 6. Install Challenger Droids (optional)

```bash
droidproxy install-droids
```

This copies the bundled `.md` files into `~/.factory/droids/` and
`~/.factory/commands/`. You can then use `/challenge-opus`,
`/challenge-gpt`, and `/challenge-gemini` in any Droid session for a
cross-model second opinion.

## 7. Enable thinking output in Factory

1. Start Factory.
2. Run `/settings`.
3. Set **Show thinking in main view: On**.

## 8. Start on login (optional)

Run DroidProxy at login as a user systemd service:

```bash
mkdir -p ~/.config/systemd/user
cp $(python -c "import droidproxy, pathlib; print(pathlib.Path(droidproxy.__file__).parent.parent.parent / 'packaging/droidproxy.service')") ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now droidproxy.service
```

Or install the desktop launcher under `~/.config/autostart/`:

```bash
mkdir -p ~/.config/autostart
cp src/packaging/droidproxy.desktop ~/.config/autostart/
```
