# DroidProxy Setup

## 1. Launch & Authenticate

1. Open DroidProxy from your Applications folder
2. Click the menu bar icon and select "Open Settings"
3. Click "Connect" next to Claude Code or Codex and complete the OAuth flow in your browser

## 2. Configure Factory

Open `~/.factory/settings.json` and add the following to the `customModels` array:

```json
"customModels": [
    {
      "model": "claude-opus-4-6",
      "id": "custom:droidproxy:opus-4-6",
      "index": 0,
      "baseUrl": "http://localhost:8317",
      "apiKey": "dummy-not-used",
      "displayName": "DroidProxy: Opus 4.6",
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
      "baseUrl": "http://localhost:8317",
      "apiKey": "dummy-not-used",
      "displayName": "DroidProxy: GPT 5.3 Codex",
      "maxOutputTokens": 128000,
      "noImageSupport": false,
      "provider": "openai"
    },
    {
      "model": "gpt-5.4",
      "id": "custom:droidproxy:gpt-5.4",
      "index": 3,
      "baseUrl": "http://localhost:8317",
      "apiKey": "dummy-not-used",
      "displayName": "DroidProxy: GPT 5.4",
      "maxOutputTokens": 128000,
      "noImageSupport": false,
      "provider": "openai"
    }
]
```

Use the standard Claude and Codex model aliases in the `model` field. Claude entries use `provider: "anthropic"`; GPT/Codex entries use `provider: "openai"`. DroidProxy applies Claude adaptive thinking and Codex reasoning effort based on the selected model and the thinking-effort setting in DroidProxy itself; it does not use `-thinking-*` model suffixes.

## 3. Configure Thinking Effort

1. Open DroidProxy Settings
2. Set the desired effort:
   - Opus 4.6: `low`, `medium`, `high`, or `max`
   - Sonnet 4.6: `low`, `medium`, or `high`
   - GPT 5.3 Codex: `low`, `medium`, `high`, or `xhigh`
   - GPT 5.4: `low`, `medium`, `high`, or `xhigh`

## 4. Enable Thinking Output

1. Start Factory
2. Run `/settings`
3. Set **Show thinking in main view: On**
