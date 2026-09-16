# scripts/install.ps1 — PowerShell installer for ToolRush on Windows
param(
    [string]$HermesHome = "$env:USERPROFILE\.hermes",
    [string]$RepoUrl = "https://github.com/lunkerchen/toolrush.git",
    [string]$Version = "v2.1.0"
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
$TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("toolrush_install_" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $TempDir -Force | Out-Null

try {
    Log-Message "Cloning ToolRush ($Version) into staging..."
    $RepoDir = Join-Path $TempDir "repo"
    try {
        & git clone --depth 1 --branch $Version $RepoUrl $RepoDir 2>$null
    } catch {
        Log-Message "Warning: Tag '$Version' not found, falling back to default branch."
        & git clone --depth 1 $RepoUrl $RepoDir
    }

    $SourceDir = Join-Path $RepoDir "v2\plugin"
    if (-not (Test-Path (Join-Path $SourceDir "plugin.yaml"))) {
        Error-Message "Source directory does not contain a valid ToolRush plugin."
        exit 1
    }

    $StagedPlugin = Join-Path $TempDir "staged_toolrush"
    Copy-Item -Path $SourceDir -Destination $StagedPlugin -Recurse -Force

    Log-Message "Verifying staged plugin with doctor..."
    $DoctorPy = Join-Path $StagedPlugin "doctor.py"
    & python $DoctorPy
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1) {
        Error-Message "Pre-install doctor check reported severe failure (exit code $LASTEXITCODE)."
    }

    if (-not (Test-Path $PluginsDir)) {
        New-Item -ItemType Directory -Path $PluginsDir -Force | Out-Null
    }

    if (Test-Path $TargetDir) {
        Log-Message "Existing installation found at $TargetDir, replacing..."
        Remove-Item -Path $TargetDir -Recurse -Force
    }

    Move-Item -Path $StagedPlugin -Destination $TargetDir -Force
    Log-Message "ToolRush successfully installed to $TargetDir!"
}
finally {
    if (Test-Path $TempDir) {
        Remove-Item -Path $TempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Log-Message "To enable in Hermes: hermes plugins enable toolrush"
exit 0
