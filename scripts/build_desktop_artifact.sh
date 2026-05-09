#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: build_desktop_artifact.sh <version>" >&2
  exit 2
fi

VERSION="$1"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

DIST_DIR="dist"
mkdir -p "$DIST_DIR"

ARCHIVE_NAME="ailouros-backend-${VERSION}.tar.gz"
ARCHIVE_PATH="$DIST_DIR/$ARCHIVE_NAME"

INCLUDE_PATHS=(
  "backend"
  "config"
  "requirements.txt"
  "orchestrator_api.py"
  "langgraph_pipeline.py"
  "pytest.ini"
)

for path in "${INCLUDE_PATHS[@]}"; do
  if [[ ! -e "$path" ]]; then
    echo "build_desktop_artifact: missing required path: $path" >&2
    exit 1
  fi
done

EXCLUDES=(
  "--exclude=__pycache__"
  "--exclude=*.pyc"
  "--exclude=*.pyo"
  "--exclude=.pytest_cache"
  "--exclude=.mypy_cache"
  "--exclude=tests"
  "--exclude=.git"
  "--exclude=.env"
  "--exclude=.env.*"
  "--exclude=*.local.json"
)

TAR_BIN=$(command -v gtar || command -v tar)
"$TAR_BIN" -czf "$ARCHIVE_PATH" "${EXCLUDES[@]}" "${INCLUDE_PATHS[@]}"

if grep -arE "sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}" --include="*.py" --include="*.json" "${INCLUDE_PATHS[@]}" >/dev/null 2>&1; then
  echo "build_desktop_artifact: refusing to publish — secret-shaped strings detected in payload" >&2
  rm -f "$ARCHIVE_PATH"
  exit 1
fi

shasum -a 256 "$ARCHIVE_PATH" | awk '{print $1}' > "${ARCHIVE_PATH}.sha256"

echo "==> built $(basename "$ARCHIVE_PATH")"
echo "    size:   $(wc -c < "$ARCHIVE_PATH" | awk '{print $1}') bytes"
echo "    sha256: $(cat "${ARCHIVE_PATH}.sha256")"
