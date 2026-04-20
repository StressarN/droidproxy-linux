#!/usr/bin/env bash
# Shared helpers for the droidproxy-linux release/AUR scripts.
# Source this from other scripts:  . "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

set -euo pipefail

# ---- path helpers -----------------------------------------------------------

# Absolute path to the repo root, regardless of where the caller ran from.
repo_root() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
    (cd "$script_dir/.." && pwd)
}

# ---- cleanup registry -------------------------------------------------------

declare -ga _DP_CLEANUP_DIRS=()

_dp_cleanup() {
    local dir
    for dir in "${_DP_CLEANUP_DIRS[@]}"; do
        [[ -n "$dir" && -d "$dir" ]] && rm -rf "$dir" || true
    done
}
trap _dp_cleanup EXIT

register_cleanup() {
    _DP_CLEANUP_DIRS+=("$1")
}

# ---- version helpers --------------------------------------------------------

current_version() {
    local repo="${1:-$(repo_root)}"
    grep -oP '^version = "\K[^"]+' "$repo/src/pyproject.toml"
}

current_pkgver() {
    local repo="${1:-$(repo_root)}"
    grep -oP '^pkgver=\K.+' "$repo/src/packaging/PKGBUILD"
}

require_semver() {
    local v="$1"
    if ! [[ "$v" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "error: version must be X.Y.Z, got: $v" >&2
        exit 2
    fi
}

# ---- pretty output ----------------------------------------------------------

section() {
    printf '\n\033[1m=== %s ===\033[0m\n' "$*"
}

info() {
    printf '\033[36m-> %s\033[0m\n' "$*"
}

warn() {
    printf '\033[33m!! %s\033[0m\n' "$*" >&2
}

fail() {
    printf '\033[31mxx %s\033[0m\n' "$*" >&2
    exit 1
}

confirm() {
    local prompt="${1:-Continue?}"
    local reply
    read -r -p "$prompt [y/N] " reply
    [[ "${reply,,}" == "y" || "${reply,,}" == "yes" ]]
}

# ---- preflight --------------------------------------------------------------

require_cmd() {
    local cmd
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || fail "missing required command: $cmd"
    done
}

require_clean_tree() {
    local repo="${1:-$(repo_root)}"
    if ! git -C "$repo" diff --quiet || ! git -C "$repo" diff --cached --quiet; then
        warn "working tree has uncommitted changes:"
        git -C "$repo" status --short >&2
        fail "commit or stash before running this command"
    fi
}

check_aur_ssh() {
    info "checking AUR SSH auth..."
    if ! ssh -q -o BatchMode=yes -o ConnectTimeout=10 \
              aur@aur.archlinux.org help >/dev/null 2>&1; then
        fail "cannot authenticate to aur@aur.archlinux.org via SSH.
    - make sure your public key is uploaded at https://aur.archlinux.org/account/
    - make sure ~/.ssh/config has a Host block for aur.archlinux.org pointing at
      the matching private key (IdentityFile ... IdentitiesOnly yes)
    - verify with: ssh -v aur@aur.archlinux.org help"
    fi
}

check_github_tag() {
    local tag="$1" remote="${2:-origin}"
    info "checking that tag $tag exists on $remote..."
    if ! git ls-remote --tags "$remote" "refs/tags/$tag" | grep -q "$tag"; then
        fail "tag $tag is not pushed to $remote.
    run:  git push $remote $tag"
    fi
}
