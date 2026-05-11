#!/usr/bin/env bash
# Install a pre-commit hook that runs validate.py before commits that touch
# templates, procedure.yaml, practice.yaml, or any script in scripts/.
#
# The hook runs the QUICK lint by default (sub-second) so commits stay fast.
# To run the full render-checking validation, use:
#     .venv/bin/python scripts/validate.py
# manually before pushing.
#
# To bypass the hook for an emergency commit:
#     git commit --no-verify
#
# Usage: bash scripts/install_pre_commit.sh

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_PATH="${SKILL_DIR}/.git/hooks/pre-commit"

if [ ! -d "${SKILL_DIR}/.git" ]; then
  echo "❌ Not a git repository: ${SKILL_DIR}"
  exit 1
fi

if [ -f "${HOOK_PATH}" ]; then
  echo "⚠️  Existing pre-commit hook at ${HOOK_PATH}"
  echo "    Backing up to ${HOOK_PATH}.bak"
  cp "${HOOK_PATH}" "${HOOK_PATH}.bak"
fi

cat > "${HOOK_PATH}" <<'HOOK'
#!/usr/bin/env bash
# Auto-installed by scripts/install_pre_commit.sh
# Runs the flex-sig-handout-generator validate.py (lint mode) on changes
# that could affect rendered output. Skip with: git commit --no-verify

set -e

SKILL_DIR="$(git rev-parse --show-toplevel)"
RELEVANT_PATTERNS='^(templates/|data/procedure\.yaml|practice\.yaml|scripts/.*\.py$)'

# Get list of files staged for commit
staged="$(git diff --cached --name-only --diff-filter=ACMR)"

# Bail early if no relevant files changed
if ! echo "${staged}" | grep -qE "${RELEVANT_PATTERNS}"; then
  exit 0
fi

PYTHON="${SKILL_DIR}/.venv/bin/python"
VALIDATE="${SKILL_DIR}/scripts/validate.py"

if [ ! -x "${PYTHON}" ] || [ ! -f "${VALIDATE}" ]; then
  echo "⚠️  pre-commit: validate.py or .venv/bin/python missing — skipping"
  exit 0
fi

echo "Running validate.py --quick (skip with: git commit --no-verify)"
if ! "${PYTHON}" "${VALIDATE}" --quick; then
  echo
  echo "❌ Validation failed. Fix the issues or commit with --no-verify."
  exit 1
fi
HOOK

chmod +x "${HOOK_PATH}"
echo "✅ Installed pre-commit hook at ${HOOK_PATH}"
echo
echo "Test it with: .venv/bin/python scripts/validate.py --quick"
echo "Bypass it with: git commit --no-verify"
