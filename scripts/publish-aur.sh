#!/usr/bin/env bash
# Publish or update droidproxy-linux packages on the AUR.
#
# Flow for each selected variant:
#   1. updpkgsums on the PKGBUILD in src/packaging/
#   2. makepkg -sri --noconfirm (installs locally as a smoke test)
#   3. droidproxy --version  (must match pkgver)
#   4. pacman -R to undo the install
#   5. makepkg --printsrcinfo > .SRCINFO
#   6. clone (or pull) ssh://aur@aur.archlinux.org/<pkgname>.git
#   7. copy PKGBUILD + .SRCINFO, commit, push
#
# Usage:
#     scripts/publish-aur.sh [--source|--bin|--both]   (default: --both)
#
# Prerequisites:
#     * v$pkgver tag pushed to your GitHub remote
#     * For --bin: DroidProxyLinux-{x86_64,aarch64}.AppImage attached to
#       the v$pkgver GitHub release (the linux-release.yml workflow does this)
#     * AUR SSH key uploaded at https://aur.archlinux.org/account/ and the
#       matching IdentityFile configured for aur.archlinux.org in ~/.ssh/config

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"

VARIANT="${1:---both}"
case "$VARIANT" in
    --source|--bin|--both) ;;
    *) fail "usage: $0 [--source|--bin|--both]" ;;
esac

REPO="$(repo_root)"
builtin cd "$REPO"

require_cmd git makepkg updpkgsums sudo ssh

PKGVER="$(current_pkgver "$REPO")"

section "Preflight"
info "pkgver from PKGBUILD: $PKGVER"
check_aur_ssh
check_github_tag "v$PKGVER"

publish_one() {
    local pkgname="$1"
    local pkgbuild_src="$2"

    section "Publishing $pkgname $PKGVER-1"

    local scratch aur_checkout
    scratch=$(mktemp -d -t "aur-$pkgname-XXXXXX")
    aur_checkout=$(mktemp -d -t "$pkgname-repo-XXXXXX")
    register_cleanup "$scratch"
    register_cleanup "$aur_checkout"

    # --- local build + install smoke ------------------------------------
    cp "$REPO/$pkgbuild_src" "$scratch/PKGBUILD"
    builtin cd "$scratch"

    info "updpkgsums"
    updpkgsums

    info "makepkg -sri"
    makepkg -sri --noconfirm

    info "droidproxy --version"
    # Run through a real tempfile so failures surface instead of being
    # swallowed by the `$()` command substitution.
    local version_out
    version_out="$(mktemp)"
    register_cleanup "$version_out"
    if ! droidproxy --version >"$version_out" 2>&1; then
        warn "droidproxy --version failed; output:"
        cat "$version_out" >&2 || true
        sudo pacman -R --noconfirm "$pkgname" || true
        fail "installed $pkgname is broken -- not pushing to AUR"
    fi
    local installed_version
    installed_version="$(awk '{print $2}' "$version_out")"
    if [[ "$installed_version" != "$PKGVER" ]]; then
        cat "$version_out" >&2 || true
        sudo pacman -R --noconfirm "$pkgname" || true
        fail "installed droidproxy --version ($installed_version) != pkgver ($PKGVER)"
    fi

    info "pacman -R $pkgname"
    sudo pacman -R --noconfirm "$pkgname"

    info "generating .SRCINFO"
    makepkg --printsrcinfo > .SRCINFO

    if command -v namcap >/dev/null 2>&1; then
        info "namcap lint"
        namcap PKGBUILD || true
    fi

    # --- AUR push --------------------------------------------------------
    info "cloning ssh://aur@aur.archlinux.org/$pkgname.git"
    git clone "ssh://aur@aur.archlinux.org/$pkgname.git" "$aur_checkout"
    builtin cd "$aur_checkout"

    cp "$scratch/PKGBUILD" .
    cp "$scratch/.SRCINFO" .

    if git diff --quiet && git diff --cached --quiet; then
        warn "$pkgname already at $PKGVER-1 on AUR, nothing to push"
        return 0
    fi

    git add PKGBUILD .SRCINFO
    if git log -1 >/dev/null 2>&1; then
        git commit -m "Bump $pkgname to $PKGVER-1"
    else
        git commit -m "Initial upload: $pkgname $PKGVER-1"
    fi
    git push origin master

    info "pushed $pkgname $PKGVER-1 to AUR"
    echo "  https://aur.archlinux.org/packages/$pkgname"
}

if [[ "$VARIANT" == "--source" || "$VARIANT" == "--both" ]]; then
    publish_one droidproxy-linux src/packaging/PKGBUILD
fi

if [[ "$VARIANT" == "--bin" || "$VARIANT" == "--both" ]]; then
    publish_one droidproxy-linux-bin src/packaging/PKGBUILD-bin
fi

section "All done"
