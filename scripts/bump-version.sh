#!/usr/bin/env bash
# Bump the droidproxy-linux version string in every place it appears,
# run tests + lint, then commit and tag.
#
# Usage:
#     scripts/bump-version.sh <new-version>
#
# Example:
#     scripts/bump-version.sh 1.8.10

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"

NEW_VERSION="${1:-}"
if [[ -z "$NEW_VERSION" ]]; then
    fail "usage: $0 <new-version>   e.g. $0 1.8.10"
fi
require_semver "$NEW_VERSION"

REPO="$(repo_root)"
builtin cd "$REPO"

require_cmd git sed grep
require_clean_tree "$REPO"

CURRENT="$(current_version "$REPO")"
if [[ "$CURRENT" == "$NEW_VERSION" ]]; then
    info "already at $NEW_VERSION, nothing to do"
    exit 0
fi
section "Bumping $CURRENT -> $NEW_VERSION"

# --- edits -------------------------------------------------------------------

sed -i "s/^version = \"$CURRENT\"$/version = \"$NEW_VERSION\"/" \
    src/pyproject.toml

sed -i "s/^__version__ = \"$CURRENT\"$/__version__ = \"$NEW_VERSION\"/" \
    src/src/droidproxy/__init__.py

for pkgbuild in src/packaging/PKGBUILD src/packaging/PKGBUILD-bin; do
    sed -i \
        -e "s/^pkgver=$CURRENT$/pkgver=$NEW_VERSION/" \
        -e 's/^pkgrel=[0-9]\+$/pkgrel=1/' \
        "$pkgbuild"
done

# --- verify the sed actually landed -----------------------------------------

for f in src/pyproject.toml src/src/droidproxy/__init__.py \
         src/packaging/PKGBUILD src/packaging/PKGBUILD-bin; do
    if ! grep -q "$NEW_VERSION" "$f"; then
        fail "$NEW_VERSION not found in $f after sed -- revert with 'git checkout -- $f'"
    fi
done

# --- tests + lint -----------------------------------------------------------

if [[ -x "$REPO/src/.venv/bin/python" ]]; then
    section "Running pytest"
    "$REPO/src/.venv/bin/python" -m pytest src/tests -q

    section "Running ruff"
    "$REPO/src/.venv/bin/ruff" check src/src src/tests
else
    warn "src/.venv not found; skipping pytest + ruff. Run them before pushing."
fi

# --- commit + tag -----------------------------------------------------------

section "Diff"
git --no-pager diff

echo
if ! confirm "Commit and tag v$NEW_VERSION?"; then
    warn "aborted. Revert with:  git checkout -- \$(git diff --name-only)"
    exit 1
fi

git add src/pyproject.toml src/src/droidproxy/__init__.py \
        src/packaging/PKGBUILD src/packaging/PKGBUILD-bin
git commit -m "chore: bump to $NEW_VERSION"
git tag -a "v$NEW_VERSION" -m "DroidProxy Linux $NEW_VERSION"

echo
info "created commit + tag v$NEW_VERSION"
echo "push with:"
echo "    git push origin \$(git rev-parse --abbrev-ref HEAD) v$NEW_VERSION"
