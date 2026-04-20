#!/usr/bin/env bash
# Build and install a PKGBUILD locally to verify it works before publishing.
#
#   --source    (default) use git-archive of HEAD so no GitHub tag is needed
#   --bin       use the published AppImages from the v$pkgver GitHub release
#
# Usage:
#     scripts/test-pkgbuild.sh [--source|--bin]

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"

VARIANT="--source"
if [[ $# -gt 0 ]]; then
    VARIANT="$1"
fi

case "$VARIANT" in
    --source|--bin) ;;
    *) fail "usage: $0 [--source|--bin]" ;;
esac

REPO="$(repo_root)"
builtin cd "$REPO"

require_cmd makepkg updpkgsums sudo

PKGVER="$(current_pkgver "$REPO")"

if [[ "$VARIANT" == "--source" ]]; then
    PKGNAME="droidproxy-linux"
    PKGBUILD_SRC="src/packaging/PKGBUILD"
    TARBALL_HOST="/tmp/droidproxy-linux-$PKGVER.tar.gz"

    section "Source variant: building $PKGNAME $PKGVER from git HEAD"
    info "packaging HEAD as $TARBALL_HOST"
    git archive --format=tar.gz \
        --prefix="$PKGNAME-$PKGVER/" \
        HEAD -o "$TARBALL_HOST"
else
    PKGNAME="droidproxy-linux-bin"
    PKGBUILD_SRC="src/packaging/PKGBUILD-bin"

    section "Binary variant: building $PKGNAME $PKGVER from GitHub release"
    check_github_tag "v$PKGVER"
fi

WORKDIR=$(mktemp -d -t "$PKGNAME-aur-XXXXXX")
register_cleanup "$WORKDIR"
cp "$PKGBUILD_SRC" "$WORKDIR/PKGBUILD"
builtin cd "$WORKDIR"

if [[ "$VARIANT" == "--source" ]]; then
    # Rewrite source= to use the local tarball so we don't need a GitHub tag.
    sed -i "s|source=.*|source=(\"$PKGNAME-$PKGVER.tar.gz::file://$TARBALL_HOST\")|" PKGBUILD
fi

section "updpkgsums"
updpkgsums

section "makepkg -sri"
makepkg -sri --noconfirm

section "droidproxy --version"
droidproxy --version

section "Uninstalling $PKGNAME"
sudo pacman -R --noconfirm "$PKGNAME"

section "Generating .SRCINFO"
makepkg --printsrcinfo > .SRCINFO

if command -v namcap >/dev/null 2>&1; then
    section "namcap"
    namcap PKGBUILD || true
    namcap "$PKGNAME-$PKGVER"-*.pkg.tar.zst || true
else
    warn "namcap not installed; skipping lint. Install with:  sudo pacman -S namcap"
fi

echo
info "PKGBUILD verified OK at $WORKDIR"
if [[ "$VARIANT" == "--source" ]]; then
    info "(cleanup: the script will remove $WORKDIR on exit; $TARBALL_HOST is kept)"
fi
