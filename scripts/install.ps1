# scripts/install.ps1 — Safe, robust PowerShell installer for ToolRush on Windows
param(
    [string]$HermesHome = "$env:USERPROFILE\.hermes",
    [string]$RepoUrl = "https://github.com/lunkerchen/toolrush.git",
    [string]$Version = "v2.1.2",
    [switch]$AllowUnpinned
)

$ErrorActionPreference = "Stop"

function Log-Message([string]$Message) {
    Write-Host "[toolrush-install] $Message"
}

function Error-Message([string]$Message) {
    Write-Error "[toolrush-install] ERROR: $Message"
}

# 1. Prerequisite checks
foreach ($cmd in @("git", "python")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Error-Message "Required command '$cmd' is not found in PATH."
        exit 1
    }
}

$PluginsDir = Join-Path $HermesHome "plugins"
$TargetDir = Join-Path $PluginsDir "toolrush"

if (-not (Test-Path $PluginsDir)) {
    New-Item -ItemType Directory -Path $PluginsDir -Force | Out-Null
}

# 2. Concurrency lock
$LockFile = Join-Path $PluginsDir ".toolrush_install.lock"
try {
    $lockStream = [System.IO.File]::Open($LockFile, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
    Error-Message "Concurrent installation in progress (or stale lockfile at $LockFile)."
    exit 1
}

$StagedPlugin = $null
$BackupDir = $null
$TempCloneDir = $null
$InstallSuccess = $false

try {
    # 3. Clone repository
    $TempCloneDir = Join-Path ([System.IO.Path]::GetTempPath()) ("toolrush_clone_" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $TempCloneDir -Force | Out-Null

    Log-Message "Cloning ToolRush ($Version)..."
    $RepoDir = Join-Path $TempCloneDir "repo"
    
    $cloneSuccess = $false
    try {
        & git clone --depth 1 --branch $Version $RepoUrl $RepoDir 2>$null
        if ($LASTEXITCODE -eq 0) { $cloneSuccess = $true }
    } catch {
        $cloneSuccess = $false
    }

    if (-not $cloneSuccess) {
        if ($AllowUnpinned) {
            Log-Message "Warning: Tag '$Version' not found, falling back to default branch because -AllowUnpinned was passed."
            & git clone --depth 1 $RepoUrl $RepoDir
            if ($LASTEXITCODE -ne 0) {
                Error-Message "Failed to clone repository from $RepoUrl."
                exit 1
            }
        } else {
            Error-Message "Tag/Version '$Version' not found or failed to clone from $RepoUrl. Uncontrolled fallback to main refused."
            exit 1
        }
    }

    $SourceDir = Join-Path $RepoDir "v2\plugin"
    if (-not (Test-Path (Join-Path $SourceDir "plugin.yaml"))) {
        Error-Message "Source directory does not contain a valid ToolRush plugin."
        exit 1
    }

    # Same-filesystem staging inside PluginsDir
    $StagedPlugin = Join-Path $PluginsDir (".toolrush_stage_" + [System.Guid]::NewGuid().ToString("N"))
    Copy-Item -Path $SourceDir -Destination $StagedPlugin -Recurse -Force

    Log-Message "Verifying staged plugin with doctor..."
    $DoctorPy = Join-Path $StagedPlugin "doctor.py"
    & python $DoctorPy
    if ($LASTEXITCODE -ne 0) {
        Error-Message "Pre-install doctor check reported severe failure (exit code $LASTEXITCODE)."
        exit 1
    }

    # 4. Atomic upgrade / rename with rollback and backup retention
    if (Test-Path $TargetDir) {
        Log-Message "Existing installation found at $TargetDir. Staging safe upgrade..."
        $BackupDir = Join-Path $PluginsDir (".toolrush_backup_" + [System.Guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
        $BackupTarget = Join-Path $BackupDir "toolrush"

        # Rename existing target to backup
        Move-Item -Path $TargetDir -Destination $BackupTarget -Force

        try {
            Move-Item -Path $StagedPlugin -Destination $TargetDir -Force
        } catch {
            Error-Message "Failed to move staged plugin into place. Performing rollback..."
            Move-Item -Path $BackupTarget -Destination $TargetDir -Force
            exit 1
        }
    } else {
        Move-Item -Path $StagedPlugin -Destination $TargetDir -Force
    }

    # 5. Post-install verification
    Log-Message "Running post-install verification..."
    $DoctorSmoke = Join-Path $TargetDir "doctor.py"
    & python $DoctorSmoke --smoke
    if ($LASTEXITCODE -ne 0) {
        Error-Message "Post-install doctor smoke check failed!"
        if ($BackupDir -and (Test-Path (Join-Path $BackupDir "toolrush"))) {
            Log-Message "Rolling back to previous installation..."
            Remove-Item -Path $TargetDir -Recurse -Force -ErrorAction SilentlyContinue
            Move-Item -Path (Join-Path $BackupDir "toolrush") -Destination $TargetDir -Force
        }
        exit 1
    }

    $InstallSuccess = $true
    Log-Message "ToolRush successfully installed and verified at $TargetDir!"
} finally {
    if ($lockStream) {
        $lockStream.Close()
        $lockStream.Dispose()
    }
    if (Test-Path $LockFile) {
        Remove-Item -Path $LockFile -Force -ErrorAction SilentlyContinue
    }
    if ($TempCloneDir -and (Test-Path $TempCloneDir)) {
        Remove-Item -Path $TempCloneDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($StagedPlugin -and (Test-Path $StagedPlugin)) {
        Remove-Item -Path $StagedPlugin -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($InstallSuccess) {
        if ($BackupDir -and (Test-Path $BackupDir)) {
            Remove-Item -Path $BackupDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    } else {
        if ($BackupDir -and (Test-Path $BackupDir)) {
            Write-Host "[toolrush-install] Existing installation preserved at $BackupDir"
        }
    }
}

Log-Message "To enable in Hermes: hermes plugins enable toolrush"
exit 0
