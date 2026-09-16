#!/usr/bin/env bash
# scripts/install.sh — Safe, robust, idempotent installer for ToolRush
set -euo pipefail

HERMES_DIR="${HERMES_HOME:-$HOME/.hermes}"
PLUGINS_DIR="${HERMES_DIR}/plugins"
TARGET_DIR="${PLUGINS_DIR}/toolrush"
REPO_URL="${TOOLRUSH_REPO:-https://github.com/lunkerchen/toolrush.git}"
VERSION_TAG="${TOOLRUSH_VERSION:-v2.1.0}"
ALLOW_UNPINNED="${TOOLRUSH_ALLOW_UNPINNED:-0}"

log() {
  printf "[toolrush-install] %s\n" "$*"
}

error() {
  printf "[toolrush-install] ERROR: %s\n" "$*" >&2
}

# 1. Prerequisite checks
for cmd in git cp mktemp python3 mkdir mv rm; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    error "Required command '$cmd' is not installed or not in PATH."
    exit 1
  fi
done

# Ensure plugins directory exists before locking or staging
mkdir -p "$PLUGINS_DIR"

# 2. Concurrency lock to refuse concurrent installations
LOCK_FILE="${PLUGINS_DIR}/.toolrush_install.lock"
if ! (set -C; : > "$LOCK_FILE") 2>/dev/null; then
  error "Concurrent installation in progress (or stale lockfile at $LOCK_FILE)."
  exit 1
fi

# 3. Setup same-filesystem staging and backup tracking
STAGE_PLUGIN=""
BACKUP_DIR=""
TEMP_CLONE_DIR=""
INSTALL_SUCCESS=0

cleanup() {
  local exit_code=$?
  rm -f "$LOCK_FILE" || true
  if [ -n "$TEMP_CLONE_DIR" ] && [ -d "$TEMP_CLONE_DIR" ]; then
    rm -rf "$TEMP_CLONE_DIR" || true
  fi
  if [ -n "$STAGE_PLUGIN" ] && [ -d "$STAGE_PLUGIN" ]; then
    rm -rf "$STAGE_PLUGIN" || true
  fi
  if [ "$INSTALL_SUCCESS" -eq 1 ]; then
    if [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ]; then
      rm -rf "$BACKUP_DIR" || true
    fi
  else
    if [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ]; then
      log "Installation failed. Existing installation preserved / retained at: $BACKUP_DIR"
    fi
  fi
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

# Same-filesystem staging directory inside PLUGINS_DIR guarantees atomic rename
STAGE_PLUGIN=$(mktemp -d "${PLUGINS_DIR}/.toolrush_stage_XXXXXX")

# Clone repository into a temporary workspace
TEMP_CLONE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/toolrush_clone_XXXXXX")
CLONE_ERR="${TEMP_CLONE_DIR}/clone_err.log"

log "Fetching ToolRush (${VERSION_TAG})..."
if git clone --depth 1 --branch "$VERSION_TAG" "$REPO_URL" "$TEMP_CLONE_DIR/repo" 2>"$CLONE_ERR"; then
  SOURCE_DIR="$TEMP_CLONE_DIR/repo/v2/plugin"
else
  if [ "$ALLOW_UNPINNED" = "1" ]; then
    error "Tag '$VERSION_TAG' clone failed; falling back to default branch because TOOLRUSH_ALLOW_UNPINNED=1."
    if git clone --depth 1 "$REPO_URL" "$TEMP_CLONE_DIR/repo" 2>"$CLONE_ERR"; then
      SOURCE_DIR="$TEMP_CLONE_DIR/repo/v2/plugin"
    else
      error "Failed to clone repository from $REPO_URL:"
      cat "$CLONE_ERR" >&2
      exit 1
    fi
  else
    error "Tag/Version '$VERSION_TAG' not found or failed to clone from $REPO_URL."
    error "Uncontrolled fallback to main is refused for safety. Set TOOLRUSH_ALLOW_UNPINNED=1 to override."
    cat "$CLONE_ERR" >&2
    exit 1
  fi
fi

if [ ! -d "$SOURCE_DIR" ] || [ ! -f "$SOURCE_DIR/plugin.yaml" ]; then
  error "Source directory does not contain a valid ToolRush plugin ($SOURCE_DIR)."
  exit 1
fi

# Stage plugin into same-filesystem directory
cp -R "$SOURCE_DIR/"* "$STAGE_PLUGIN/"

# Verify staging with doctor pre-flight
log "Verifying staged plugin integrity with doctor..."
if ! python3 "$STAGE_PLUGIN/doctor.py" >/dev/null 2>&1; then
  error "Staged plugin failed doctor pre-install verification."
  exit 1
fi

# 4. Safe upgrade / atomic rename with rollback and backup retention
if [ -d "$TARGET_DIR" ]; then
  log "Existing ToolRush installation found. Staging safe upgrade..."
  BACKUP_DIR=$(mktemp -d "${PLUGINS_DIR}/.toolrush_backup_XXXXXX")
  
  # Same-filesystem atomic move to backup (never rm -rf before move!)
  if ! mv "$TARGET_DIR" "${BACKUP_DIR}/toolrush"; then
    error "Failed to move existing target to backup directory."
    exit 1
  fi

  # Atomic replace from stage
  if ! mv "$STAGE_PLUGIN" "$TARGET_DIR"; then
    error "Failed to move staged plugin into place. Performing immediate rollback..."
    mv "${BACKUP_DIR}/toolrush" "$TARGET_DIR" || error "CRITICAL: Rollback failed; backup safely retained at ${BACKUP_DIR}/toolrush"
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
if ! python3 "$TARGET_DIR/doctor.py" --smoke >/dev/null 2>&1; then
  error "Post-install doctor smoke check failed!"
  if [ -n "$BACKUP_DIR" ] && [ -d "${BACKUP_DIR}/toolrush" ]; then
    error "Rolling back to previous installation due to verification failure..."
    rm -rf "$TARGET_DIR" || true
    mv "${BACKUP_DIR}/toolrush" "$TARGET_DIR" || error "CRITICAL: Rollback failed; previous install retained at ${BACKUP_DIR}/toolrush"
  fi
  exit 1
fi

INSTALL_SUCCESS=1
log "ToolRush installation succeeded and verified!"
log "To enable ToolRush in Hermes: hermes plugins enable toolrush"
exit 0
