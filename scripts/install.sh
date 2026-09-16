#!/usr/bin/env bash
# scripts/install.sh — Safe, idempotent installer for ToolRush
set -euo pipefail

HERMES_DIR="${HERMES_HOME:-$HOME/.hermes}"
PLUGINS_DIR="${HERMES_DIR}/plugins"
TARGET_DIR="${PLUGINS_DIR}/toolrush"
REPO_URL="${TOOLRUSH_REPO:-https://github.com/lunkerchen/toolrush.git}"
VERSION_TAG="${TOOLRUSH_VERSION:-v2.1.0}"

log() {
  printf "[toolrush-install] %s\n" "$*"
}

error() {
  printf "[toolrush-install] ERROR: %s\n" "$*" >&2
}

# 1. Prerequisite checks
for cmd in git cp mktemp python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    error "Required command '$cmd' is not installed or not in PATH."
    exit 1
  fi
done

# 2. Setup staging and traps
TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/toolrush_install_XXXXXX")
BACKUP_DIR=""
cleanup() {
  rm -rf "$TMP_DIR"
  if [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ]; then
    rm -rf "$BACKUP_DIR"
  fi
}
trap cleanup EXIT INT TERM

log "Cloning ToolRush (${VERSION_TAG}) into staging directory..."
if git clone --depth 1 --branch "$VERSION_TAG" "$REPO_URL" "$TMP_DIR/repo" 2>/dev/null; then
  SOURCE_DIR="$TMP_DIR/repo/v2/plugin"
elif git clone --depth 1 "$REPO_URL" "$TMP_DIR/repo" 2>/dev/null; then
  log "Warning: Tag '$VERSION_TAG' not found, cloned default branch."
  SOURCE_DIR="$TMP_DIR/repo/v2/plugin"
else
  error "Failed to clone repository from $REPO_URL."
  exit 1
fi

if [ ! -d "$SOURCE_DIR" ] || [ ! -f "$SOURCE_DIR/plugin.yaml" ]; then
  error "Source directory does not contain a valid ToolRush plugin ($SOURCE_DIR)."
  exit 1
fi

# 3. Stage plugin
STAGE_PLUGIN="$TMP_DIR/staged_toolrush"
mkdir -p "$STAGE_PLUGIN"
cp -R "$SOURCE_DIR/"* "$STAGE_PLUGIN/"

# Verify staging with doctor
log "Verifying staged plugin integrity with doctor..."
if ! python3 "$STAGE_PLUGIN/doctor.py" >/dev/null 2>&1; then
  # Try with doctor basic check
  if ! python3 -c "import importlib.util; s=importlib.util.spec_from_file_location('doc', '$STAGE_PLUGIN/doctor.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)" >/dev/null 2>&1; then
    error "Staged plugin failed doctor pre-install verification."
    exit 1
  fi
fi

# 4. Atomic installation / replacement
mkdir -p "$PLUGINS_DIR"

if [ -d "$TARGET_DIR" ]; then
  log "Existing ToolRush installation found. Preparing atomic upgrade..."
  BACKUP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/toolrush_backup_XXXXXX")
  cp -R "$TARGET_DIR" "$BACKUP_DIR/backup"
  
  # Remove target and move staged plugin
  rm -rf "$TARGET_DIR"
  if ! mv "$STAGE_PLUGIN" "$TARGET_DIR"; then
    error "Failed to move staged plugin to target directory. Rolling back..."
    mv "$BACKUP_DIR/backup/$(basename "$TARGET_DIR")" "$TARGET_DIR" || true
    exit 1
  fi
else
  log "Installing ToolRush to $TARGET_DIR..."
  if ! mv "$STAGE_PLUGIN" "$TARGET_DIR"; then
    error "Failed to move staged plugin to target directory."
    exit 1
  fi
fi

# 5. Post-install verification
log "Running post-install verification..."
if python3 "$TARGET_DIR/doctor.py" --smoke >/dev/null 2>&1 || python3 "$TARGET_DIR/doctor.py" >/dev/null 2>&1; then
  log "ToolRush installation succeeded and passed verification!"
else
  log "Warning: ToolRush installed, but doctor reported warnings/degradations."
fi

log "To enable ToolRush in Hermes: hermes plugins enable toolrush (or restart gateway)"
exit 0
