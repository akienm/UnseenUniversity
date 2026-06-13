#!/usr/bin/env bash
# intent: shell script: test_pre_commit_guard.sh
# test_pre_commit_guard.sh — exercises lab/claudecode/hooks/pre-commit rules.
#
# Creates a temp git repo, installs the hook, stages files that should be
# rejected, and asserts the hook exits nonzero with the expected reason.
# Also stages a normal file and asserts accept.
#
# Run: bash tests/hooks/test_pre_commit_guard.sh

set -u

REPO_ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")/../.." rev-parse --show-toplevel)"
HOOK="$REPO_ROOT/lab/claudecode/hooks/pre-commit"

if [ ! -x "$HOOK" ]; then
    echo "FAIL: hook not executable at $HOOK" >&2
    exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cd "$TMP"
git init -q -b main
git config user.email "test@test"
git config user.name "test"
cp "$HOOK" .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit

# Establish a base commit so subsequent commits have parents.
echo "hello" > .ok_base
git add .ok_base
git commit -qm "base" || true

pass=0
fail=0
_assert_reject() {
    local name="$1"; shift
    # $1 = file path to create and stage, $2 = content
    local path="$1" content="${2:-x}"
    mkdir -p "$(dirname "$path")" 2>/dev/null || true
    printf '%s\n' "$content" > "$path"
    git add -f "$path" 2>/dev/null
    if git commit -qm "should reject $name" 2>/dev/null; then
        echo "FAIL: $name was accepted but should have been rejected" >&2
        fail=$((fail+1))
    else
        echo "PASS: $name rejected as expected"
        pass=$((pass+1))
    fi
    git reset -q HEAD
    rm -rf "$path"
}

_assert_accept() {
    local name="$1" path="$2" content="${3:-hello}"
    printf '%s\n' "$content" > "$path"
    git add "$path"
    if git commit -qm "should accept $name" 2>/dev/null; then
        echo "PASS: $name accepted as expected"
        pass=$((pass+1))
    else
        echo "FAIL: $name was rejected but should have been accepted" >&2
        fail=$((fail+1))
    fi
}

_assert_reject "env-file"             ".env" "SECRET=x"
_assert_reject "env-variant"          ".env.local" "SECRET=y"
_assert_reject "sqlite-db"            "some.db" "binary"
_assert_reject "decisions-log-direct" "lab/design_docs_for_igor/decisions_log.dsb" "fake entry"
_assert_accept "normal-python-file"   "foo.py" "print('ok')"

echo "---"
echo "RESULTS: $pass pass, $fail fail"
exit $fail
