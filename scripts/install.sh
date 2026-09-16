#!/usr/bin/env bash
# scripts/install.sh — Safe, robust, idempotent installer for ToolRush
set -euo pipefail

HERMES_DIR="${HERMES_HOME:-$HOME/.hermes}"
PLUGINS_DIR="${HERMES_DIR}/plugins"
TARGET_DIR="${PLUGINS_DIR}/toolrush"
REPO_URL="${TOOLRUSH_REPO:-https://github.com/lunkerchen/toolrush.git}"
VERSION_TAG="${TOOLRUSH_VERSION:-v2.1.0}"
ALLOW_UNPINNED="${TOOLRUSH_ALLOW_UNPINNED:-0}"
# Release artifact: local path or http(s) URL. Empty means "install from git".
RELEASE_ARCHIVE="${TOOLRUSH_RELEASE_ARCHIVE:-}"
EXPECTED_SHA256="${TOOLRUSH_EXPECTED_SHA256:-}"
# Optional explicit SHA256SUMS location (path or URL); otherwise discovered next to the archive.
SHA256SUMS_SRC="${TOOLRUSH_SHA256SUMS:-}"

log() {
  printf "[toolrush-install] %s\n" "$*"
}

error() {
  printf "[toolrush-install] ERROR: %s\n" "$*" >&2
}

# 1. Prerequisite checks
REQUIRED_CMDS=(cp mktemp python3 mkdir mv rm)
if [ -n "$RELEASE_ARCHIVE" ]; then
  case "$RELEASE_ARCHIVE" in
    http://*|https://*) REQUIRED_CMDS+=(curl) ;;
  esac
else
  REQUIRED_CMDS+=(git)
fi
for cmd in "${REQUIRED_CMDS[@]}"; do
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
CLEANUP_DONE=0

# cleanup <exit_code>. EXIT passes 0 as a placeholder and the real status is
# recovered from $?; INT/TERM pass their signal exit codes explicitly.
cleanup() {
  local status=$?
  local exit_code="${1:-$status}"
  if [ "$exit_code" -eq 0 ]; then
    exit_code=$status
  fi

  if [ "$CLEANUP_DONE" -eq 1 ]; then
    exit "$exit_code"
  fi
  CLEANUP_DONE=1
  trap - EXIT INT TERM

  if [ "$exit_code" -ne 0 ] || [ "$INSTALL_SUCCESS" -ne 1 ]; then
    # Install did not reach INSTALL_SUCCESS=1: put the previous installation back.
    # A surviving ${BACKUP_DIR}/toolrush means the target was moved aside and
    # never restored by an inline rollback.
    if [ -n "$BACKUP_DIR" ] && [ -d "${BACKUP_DIR}/toolrush" ]; then
      [ -d "$TARGET_DIR" ] && rm -rf "$TARGET_DIR"
      if mv "${BACKUP_DIR}/toolrush" "$TARGET_DIR"; then
        log "Previous ToolRush installation restored to $TARGET_DIR"
        rmdir "$BACKUP_DIR" 2>/dev/null || true
      else
        error "CRITICAL: Rollback failed; previous install retained at ${BACKUP_DIR}/toolrush"
      fi
    elif [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ]; then
      rmdir "$BACKUP_DIR" 2>/dev/null || true
    fi
    [ -n "$STAGE_PLUGIN" ] && [ -d "$STAGE_PLUGIN" ] && rm -rf "$STAGE_PLUGIN" || true
    [ -n "$TEMP_CLONE_DIR" ] && [ -d "$TEMP_CLONE_DIR" ] && rm -rf "$TEMP_CLONE_DIR" || true
  else
    [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ] && rm -rf "$BACKUP_DIR" || true
    [ -n "$STAGE_PLUGIN" ] && [ -d "$STAGE_PLUGIN" ] && rm -rf "$STAGE_PLUGIN" || true
    [ -n "$TEMP_CLONE_DIR" ] && [ -d "$TEMP_CLONE_DIR" ] && rm -rf "$TEMP_CLONE_DIR" || true
  fi

  # Lock is released last, so no concurrent installer can race the restore above.
  rm -f "$LOCK_FILE" || true
  exit "$exit_code"
}
trap "cleanup 0" EXIT
trap "cleanup 130" INT
trap "cleanup 143" TERM

# Same-filesystem staging directory inside PLUGINS_DIR guarantees atomic rename
STAGE_PLUGIN=$(mktemp -d "${PLUGINS_DIR}/.toolrush_stage_XXXXXX")
TEMP_CLONE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/toolrush_work_XXXXXX")

sha256_of() {
  python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"
}

# fetch <url> <dest>; bounded by connect/total timeouts and a retry cap.
fetch() {
  curl --fail --location --silent --show-error \
    --connect-timeout 10 --max-time 300 \
    --retry 3 --retry-delay 2 --retry-max-time 120 \
    --output "$2" "$1"
}

# Reject traversal, absolute paths, and symlinks before writing anything.
extract_zip_safely() {
  python3 - "$1" "$2" <<'PY'
import os
import stat
import sys
import zipfile

archive, dest = sys.argv[1], sys.argv[2]
dest_root = os.path.realpath(dest)

with zipfile.ZipFile(archive) as zf:
    for member in zf.infolist():
        name = member.filename
        if not name or name.startswith("/") or name.startswith("\\") or "\\" in name:
            sys.exit(f"unsafe archive member (absolute path or backslash): {name!r}")
        if os.path.isabs(name) or os.path.splitdrive(name)[0]:
            sys.exit(f"unsafe archive member (absolute path): {name!r}")
        parts = [p for p in name.split("/") if p]
        if any(p == ".." for p in parts):
            sys.exit(f"unsafe archive member (path traversal): {name!r}")

        is_symlink = getattr(member, "is_symlink", None)
        if callable(is_symlink):
            if is_symlink():
                sys.exit(f"unsafe archive member (symlink): {name!r}")
        elif stat.S_ISLNK(member.external_attr >> 16):
            sys.exit(f"unsafe archive member (symlink): {name!r}")

        mode = member.external_attr >> 16
        if mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            sys.exit(f"unsafe archive member (not a regular file): {name!r}")

        target = os.path.realpath(os.path.join(dest_root, *parts))
        if target != dest_root and not target.startswith(dest_root + os.sep):
            sys.exit(f"unsafe archive member (escapes destination): {name!r}")

        if member.is_dir():
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(member) as src, open(target, "wb") as out:
            out.write(src.read())
        if mode & stat.S_IXUSR:
            os.chmod(target, 0o755)
PY
}

if [ -n "$RELEASE_ARCHIVE" ]; then
  # 3a. Install from a pinned release artifact
  ARCHIVE_FILE="${TEMP_CLONE_DIR}/toolrush-release.zip"
  SUMS_FILE=""
  case "$RELEASE_ARCHIVE" in
    http://*|https://*)
      log "Downloading ToolRush release archive from $RELEASE_ARCHIVE ..."
      if ! fetch "$RELEASE_ARCHIVE" "$ARCHIVE_FILE"; then
        error "Failed to download release archive from $RELEASE_ARCHIVE"
        exit 1
      fi
      if [ -z "$EXPECTED_SHA256" ]; then
        SUMS_URL="${SHA256SUMS_SRC:-${RELEASE_ARCHIVE%/*}/SHA256SUMS}"
        if fetch "$SUMS_URL" "${TEMP_CLONE_DIR}/SHA256SUMS" 2>/dev/null; then
          SUMS_FILE="${TEMP_CLONE_DIR}/SHA256SUMS"
        fi
      fi
      ;;
    *)
      if [ ! -f "$RELEASE_ARCHIVE" ]; then
        error "Release archive not found: $RELEASE_ARCHIVE"
        exit 1
      fi
      log "Using local ToolRush release archive $RELEASE_ARCHIVE ..."
      cp "$RELEASE_ARCHIVE" "$ARCHIVE_FILE"
      if [ -z "$EXPECTED_SHA256" ]; then
        CANDIDATE="${SHA256SUMS_SRC:-$(dirname "$RELEASE_ARCHIVE")/SHA256SUMS}"
        [ -f "$CANDIDATE" ] && SUMS_FILE="$CANDIDATE"
      fi
      ;;
  esac

  ACTUAL_SHA256=$(sha256_of "$ARCHIVE_FILE")
  if [ -z "$EXPECTED_SHA256" ] && [ -n "$SUMS_FILE" ]; then
    ARCHIVE_BASENAME=$(basename "$RELEASE_ARCHIVE")
    EXPECTED_SHA256=$(awk -v f="$ARCHIVE_BASENAME" '$2 == f || $2 == "*" f {print $1; exit}' "$SUMS_FILE")
    if [ -z "$EXPECTED_SHA256" ]; then
      error "SHA256SUMS at $SUMS_FILE has no entry for $ARCHIVE_BASENAME."
      exit 1
    fi
  fi

  if [ -n "$EXPECTED_SHA256" ]; then
    if [ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]; then
      error "Release archive checksum mismatch."
      error "  expected: $EXPECTED_SHA256"
      error "  actual:   $ACTUAL_SHA256"
      exit 1
    fi
    log "Archive checksum verified ($ACTUAL_SHA256)."
  else
    log "WARNING: no checksum available; installing unverified archive ($ACTUAL_SHA256)."
  fi

  log "Extracting release archive..."
  if ! extract_zip_safely "$ARCHIVE_FILE" "$STAGE_PLUGIN"; then
    error "Refused to extract release archive (unsafe or corrupt members)."
    exit 1
  fi
else
  # 3b. Install from a pinned git tag
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

  if [ ! -d "$SOURCE_DIR" ]; then
    error "Source directory does not contain a valid ToolRush plugin ($SOURCE_DIR)."
    exit 1
  fi

  # Stage plugin into same-filesystem directory
  cp -R "$SOURCE_DIR/"* "$STAGE_PLUGIN/"
fi

if [ ! -f "$STAGE_PLUGIN/plugin.yaml" ] || [ ! -f "$STAGE_PLUGIN/doctor.py" ]; then
  error "Staged payload is not a valid ToolRush plugin (missing plugin.yaml or doctor.py)."
  exit 1
fi

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
  fi
  exit 1
fi

INSTALL_SUCCESS=1
log "ToolRush installation succeeded and verified!"
log "To enable ToolRush in Hermes: hermes plugins enable toolrush"
exit 0
