# AGENTS.md

## Build & Run

```bash
# Debug build (Swift)
cd src && swift build

# Release build + code-signed .app bundle
# Picks up CODESIGN_IDENTITY and APP_VERSION from env, or auto-detects
./create-app-bundle.sh

# Release build + signed + zipped for distribution
./scripts/create-release.sh <version>
```

The Swift package lives in `src/` (not the repo root). All `swift build` / `swift package` commands must run from there.

### Notarization (local)

```bash
ditto -c -k --sequesterRsrc --keepParent "DroidProxy.app" "DroidProxy-notarize.zip"
xcrun notarytool submit "DroidProxy-notarize.zip" --keychain-profile "notarytool" --wait
xcrun stapler staple "DroidProxy.app"
```

### Sparkle update signing

```bash
src/.build/artifacts/sparkle/Sparkle/bin/sign_update DroidProxy-arm64.zip
```

Reads the EdDSA private key from the system keychain automatically.

## Architecture

DroidProxy is a macOS menu bar app (LSUIElement) that runs two local servers:

1. **ThinkingProxy** (port 8317) -- A raw TCP proxy written with NWListener/NWConnection. This is the user-facing endpoint. It intercepts Anthropic API requests, parses `-thinking-N` model name suffixes, and injects `thinking`, `output_config`, and `anthropic-beta` headers before forwarding to the backend. Also handles Amp CLI path rewriting and management request forwarding to ampcode.com.

2. **CLIProxyAPIPlus** (port 8318) -- A bundled Go binary (`src/Sources/Resources/cli-proxy-api-plus`) that handles OAuth token management, provider routing, and the actual upstream API calls. Managed as a child process by `ServerManager`.

Request flow: `Client :8317 → ThinkingProxy (transform) → CLIProxyAPI :8318 → Anthropic/upstream`

### Key files

| File | Role |
|---|---|
| `ThinkingProxy.swift` | The HTTP proxy that does all request/response transformation. Surgical JSON string manipulation (not re-serialization) to preserve Anthropic prompt cache key ordering. |
| `ServerManager.swift` | Lifecycle management for the CLIProxyAPIPlus child process. Handles start/stop, orphan cleanup, config merging, provider enable/disable. |
| `AppDelegate.swift` | Menu bar UI, window management, Sparkle updater, auth directory monitoring. |
| `SettingsView.swift` | SwiftUI settings panel -- auth status, provider toggles, server controls. |
| `AuthStatus.swift` | Scans `~/.cli-proxy-api/*.json` for OAuth credential files, tracks expiry per account. |
| `AppPreferences.swift` | UserDefaults-backed preferences (e.g. `forceMaxOpus46Effort`). |
| `config.yaml` | CLIProxyAPIPlus server config. Merged at runtime with provider exclusions into `~/.cli-proxy-api/merged-config.yaml`. |

### Model suffix convention

Model names use suffixes to control thinking behavior:
- `claude-opus-4-6-thinking-128000` -- adaptive thinking, 128K budget
- `claude-opus-4-6-thinking-128000-max` -- adaptive thinking, max effort
- `claude-sonnet-4-5-20250929-thinking-5000` -- fixed budget thinking

ThinkingProxy strips the suffix, sets the clean model name, and injects the appropriate `thinking` and `output_config` JSON fields.

### Adaptive vs fixed thinking

Models containing `opus-4-6`, `opus-4-7`, `sonnet-4-6`, `sonnet-4-7` use adaptive thinking (`"type":"adaptive"`) with an `output_config.effort` field. All other Claude models use fixed budget thinking (`"type":"enabled","budget_tokens":N`). The interleaved thinking beta header is added for all thinking models except Opus 4.6.

## Release process

Releases go to `github.com/anand-92/droidproxy`. The CI workflow (`.github/workflows/release.yml`) triggers on `v*` tags and handles build/sign/notarize/DMG/Sparkle signing/GitHub release automatically. For manual local releases:

1. Build: `./create-app-bundle.sh`
2. Notarize the .app
3. Create ZIP and DMG artifacts
4. Sign ZIP with Sparkle's `sign_update`
5. Update `appcast.xml` with new version entry (Sparkle auto-update feed)
6. Tag, push, create GitHub release with artifacts

## Conventions

- Logging uses `NSLog` throughout (not `os_log` or `print`)
- JSON modification in ThinkingProxy uses surgical string replacement (regex) instead of parse-serialize to preserve key ordering for Anthropic prompt cache compatibility
- The app bundle name is `DroidProxy.app`, the Swift target is `CLIProxyMenuBar`, the bundle ID is `com.droidproxy.app`
- Auth credentials live in `~/.cli-proxy-api/` as JSON files with a `type` field (e.g. `"claude"`)
- Config hot-reload: CLIProxyAPIPlus watches `config.yaml` for changes, so provider enable/disable takes effect without restart
