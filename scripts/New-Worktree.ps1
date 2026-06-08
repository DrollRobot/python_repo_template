#Requires -Version 5.1
<#
.SYNOPSIS
    Create an isolated git worktree on a fresh branch, generate a VS Code
    workspace (copied from the repo's existing one where possible), and open it.

.DESCRIPTION
    Intended for running multiple agents in parallel: each gets its own
    checkout on its own branch, forked from current upstream. The generated
    .code-workspace is forced to point only at its own worktree and is kept
    out of git via the shared .git/info/exclude.

.PARAMETER Slug
    Short name for the work, e.g. "issue-42" or "fix/login".

.PARAMETER Base
    Branch to fork from. Defaults to $env:WT_BASE, then "main".

.PARAMETER NoBootstrap
    Skip the per-worktree dependency install.

.EXAMPLE
    .\New-Worktree.ps1 issue-42

.EXAMPLE
    .\New-Worktree.ps1 fix/login -Base develop

.NOTES
    Env overrides: WT_HOME (parent dir for worktrees), WT_BASE (default base
    branch), WT_PREFIX (branch prefix, default "agent/").
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateScript({
        if ($_ -notmatch '^[A-Za-z0-9._/-]+$') {
            throw "Slug may only contain letters, digits, and . _ / - characters."
        }
        if ($_ -like '*..*') {
            throw "Slug may not contain '..'."
        }
        $true
    })]
    [string]$Slug,

    [Parameter(Position = 1)]
    [string]$Base = $(if ($env:WT_BASE) { $env:WT_BASE } else { 'dev' }),

    [switch]$NoBootstrap
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# --- helpers ----------------------------------------------------------------

# $ErrorActionPreference = 'Stop' does NOT halt on a failing native command;
# it only governs cmdlets/terminating errors. So wrap git and check the exit
# code ourselves. Reset the GLOBAL $LASTEXITCODE first: it is only ever
# updated by a native command, so a stale value can otherwise linger. Using
# $global: (not a bare assignment) avoids creating a local that would shadow
# the engine-updated global and pin the check to 0.
function Invoke-Git {
    $global:LASTEXITCODE = 0
    $output = & git @args
    if ($LASTEXITCODE -ne 0) {
        throw "git $($args -join ' ') failed (exit $LASTEXITCODE)"
    }
    $output
}

# Find a workspace to use as a template. Prefer one already in the new
# worktree (a committed workspace, checked out from the branch) over one in
# the main repo root. Never returns our own target file.
function Get-SourceWorkspace {
    param(
        [string[]]$SearchDirs,
        [string]$ExcludeName
    )
    foreach ($dir in $SearchDirs) {
        $hit = Get-ChildItem -LiteralPath $dir -Filter '*.code-workspace' -File -ErrorAction SilentlyContinue |
               Where-Object { $_.Name -ne $ExcludeName } |
               Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

# --- resolve paths ----------------------------------------------------------

$repoRoot  = Invoke-Git rev-parse --show-toplevel       # fails fast if not in a repo
$commonDir = Invoke-Git rev-parse --git-common-dir      # shared .git, not the worktree stub
$repoName  = Split-Path $repoRoot -Leaf

$prefix  = if ($env:WT_PREFIX) { $env:WT_PREFIX } else { 'wt/' }
$branch  = "$prefix$Slug"
$dirSlug = $Slug -replace '/', '-'
$wtHome  = if ($env:WT_HOME) { $env:WT_HOME }
           else { Join-Path (Split-Path $repoRoot -Parent) "$repoName-wt" }
$wtPath  = Join-Path $wtHome $dirSlug

# --- guards -----------------------------------------------------------------

if (Test-Path -LiteralPath $wtPath) {
    throw "$wtPath already exists"
}
if (Invoke-Git branch --list $branch) {
    throw "branch $branch already exists"
}

# --- create the worktree ----------------------------------------------------

Write-Host "Fetching origin..."
Invoke-Git fetch --quiet origin

Write-Host "Creating worktree: $wtPath  (branch $branch <- origin/$Base)"
New-Item -ItemType Directory -Path $wtHome -Force | Out-Null
Invoke-Git worktree add -b $branch $wtPath "origin/$Base"

# --- generate the workspace -------------------------------------------------

$wsFile = Join-Path $wtPath "$dirSlug.code-workspace"
$srcWs  = Get-SourceWorkspace -SearchDirs @($wtPath, $repoRoot) -ExcludeName "$dirSlug.code-workspace"

$ws = $null
if ($srcWs) {
    try {
        # NB: VS Code workspace files are often JSONC (// comments, trailing
        # commas), which ConvertFrom-Json rejects on both 5.1 and 7. Fall back
        # to a minimal workspace rather than crashing.
        $ws = Get-Content -Raw -LiteralPath $srcWs | ConvertFrom-Json
        Write-Host "Workspace template: $srcWs"
    }
    catch {
        Write-Warning "Could not parse $srcWs ($($_.Exception.Message)); generating a minimal workspace."
        $ws = $null
    }
}
if (-not $ws) {
    $ws = [pscustomobject]@{ settings = @{} }
}

# Guard: the workspace must point only at this worktree. -Force overwrites any
# existing 'folders' (and adds it if absent), version-safely.
$ws | Add-Member -NotePropertyName folders -NotePropertyValue @([pscustomobject]@{ path = '.' }) -Force

# -Depth 32: ConvertTo-Json defaults to depth 2 and silently stringifies
# anything deeper (e.g. nested settings). -Encoding utf8: keep 5.1 from
# writing UTF-16LE, which 'code' won't like.
$ws | ConvertTo-Json -Depth 32 | Set-Content -LiteralPath $wsFile -Encoding utf8

# Keep generated workspace files out of git. info/exclude lives in the shared
# .git and is never committed, so this covers every worktree without touching
# the tracked .gitignore.
$exclude = Join-Path $commonDir 'info/exclude'
$pattern = '*.code-workspace'
if (-not (Test-Path -LiteralPath $exclude) -or
    -not (Select-String -LiteralPath $exclude -Pattern ([regex]::Escape($pattern)) -SimpleMatch -Quiet)) {
    Add-Content -LiteralPath $exclude -Value $pattern
}

# --- bootstrap dependencies -------------------------------------------------

if (-not $NoBootstrap) {

    # Link local .vscode/launch.json and settings.json into the worktree. Skip
    # any that already exist: a committed .vscode file is checked out by the
    # worktree, and we don't clobber tracked files.
    $vscodeSrc = Join-Path $repoRoot '.vscode'
    $vscodeDst = Join-Path $wtPath '.vscode'
    foreach ($name in @('launch.json', 'settings.json')) {
        $src = Join-Path $vscodeSrc $name
        $dst = Join-Path $vscodeDst $name
        if ((Test-Path -LiteralPath $src) -and -not (Test-Path -LiteralPath $dst)) {
            New-Item -ItemType Directory -Path $vscodeDst -Force | Out-Null   # idempotent
            try {
                New-Item -ItemType SymbolicLink -Path $dst -Target $src -ErrorAction Stop | Out-Null
                Write-Host "Linked .vscode/$name"
            }
            catch {
                Copy-Item -LiteralPath $src -Destination $dst
                Write-Host "Symlink unavailable; copied .vscode/$name instead"
            }
        }
    }

    # symlink every .env / .env.* file (testing, production, ...) so each worktree
    # shares the repo's single copy. Skip .env.example template.
    $envFiles = Get-ChildItem -LiteralPath $repoRoot -File -Force -ErrorAction SilentlyContinue |
                Where-Object { ($_.Name -eq '.env' -or $_.Name -like '.env.*') -and $_.Name -notlike '*.example' }
    foreach ($envFile in $envFiles) {
        $envLink = Join-Path $wtPath $envFile.Name
        try {
            New-Item -ItemType SymbolicLink -Path $envLink -Target $envFile.FullName -ErrorAction Stop | Out-Null
            Write-Host "Linked $($envFile.Name)"
        }
        catch {
            Copy-Item -LiteralPath $envFile.FullName -Destination $envLink
            Write-Host "Symlink unavailable; copied $($envFile.Name) instead"
        }
    }

    # uv sync / npm ci
    Push-Location $wtPath
    try {
        if ((Test-Path uv.lock) -or (Test-Path pyproject.toml)) {
            if (Get-Command uv -ErrorAction SilentlyContinue) {
                Write-Host "Bootstrapping: uv sync"; uv sync
            }
        }
        elseif (Test-Path package-lock.json) {
            if (Get-Command npm -ErrorAction SilentlyContinue) {
                Write-Host "Bootstrapping: npm ci"; npm ci
            }
        }
    }
    finally { Pop-Location }
}

# --- open VS Code -----------------------------------------------------------

if (Get-Command code -ErrorAction SilentlyContinue) {
    & code $wsFile
}
else {
    Write-Host "VS Code 'code' command not found; open manually: $wsFile"
}
