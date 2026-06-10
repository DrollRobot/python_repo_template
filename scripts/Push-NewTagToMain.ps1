#Requires -Version 7.4

<#
.SYNOPSIS
    Interactively merge the current branch into main, bump the version, tag, and push.

.DESCRIPTION
    Walks through the release process one step at a time. Before each action it
    shows what is about to happen and prompts for confirmation (y/n); answering
    'n' aborts without making any further changes. The output of every git and
    uv command is shown so the process can be watched as it happens.

    Along the way it reports the original branch, the working-tree status, and
    the current and target project versions.

    The new version can either be bumped semantically (patch/minor/major) or set
    to an explicit version number with -Version.

.PARAMETER Bump
    Semantic version bump level. One of:
      patch — bug fixes only           (1.4.2 -> 1.4.3)
      minor — new features, no breaks  (1.4.2 -> 1.5.0)
      major — breaking changes         (1.4.2 -> 2.0.0)

.PARAMETER Version
    An explicit version number to set (e.g. 1.5.0). Use instead of -Bump.

.EXAMPLE
    .\Push-PythonNewTag.ps1 patch

.EXAMPLE
    .\Push-PythonNewTag.ps1 -Version 2.0.0

.NOTES
    Requirements:
      - PowerShell 7.4 or later.
      - Run from inside the source branch.
      - `uv` installed and the project uses uv for version management.
      - Push access to origin for both main and the source branch.
#>

[CmdletBinding(DefaultParameterSetName = 'Bump')]
param(
    [Parameter(ParameterSetName = 'Bump', Mandatory, Position = 0)]
    [ValidateSet('patch', 'minor', 'major')]
    [string]$Bump,

    [Parameter(ParameterSetName = 'Version', Mandatory)]
    [ValidatePattern('^\d+\.\d+\.\d+')]
    [string]$Version
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

$useBump = $PSCmdlet.ParameterSetName -eq 'Bump'

# --- helpers ---------------------------------------------------------------

function Write-Section {
    param([string]$Title)
    Write-Host ''
    Write-Host "== $Title ==" -ForegroundColor Cyan
}

function Write-Info {
    param([string]$Label, [string]$Value)
    Write-Host ("  {0,-18}" -f "${Label}:") -NoNewline -ForegroundColor DarkGray
    Write-Host $Value -ForegroundColor White
}

function Write-Run {
    param([string]$CommandText)
    Write-Host "  > $CommandText" -ForegroundColor DarkGray
}

function Confirm-Step {
    param([string]$Prompt)
    while ($true) {
        $answer = (Read-Host "$Prompt [y/n]").Trim().ToLowerInvariant()
        switch ($answer) {
            { $_ -in 'y', 'yes' } { return $true }
            { $_ -in 'n', 'no' } { return $false }
            default { Write-Host "  Please answer 'y' or 'n'." -ForegroundColor Yellow }
        }
    }
}

# Prompt before running an action. Answering 'n' aborts the whole script and
# reports where the repository was left, since earlier steps are not undone.
function Invoke-Step {
    param(
        [string]$Prompt,
        [scriptblock]$Action
    )
    if (-not (Confirm-Step $Prompt)) {
        Write-Host ''
        Write-Host "Aborted by user." -ForegroundColor Yellow
        $branch = git branch --show-current
        Write-Host "Repository is currently on branch '$branch'." -ForegroundColor Yellow
        Write-Host "Any steps already completed above have NOT been undone." -ForegroundColor Yellow
        exit 1
    }
    & $Action
}

# --- gather state ----------------------------------------------------------

Write-Section "Release setup"

# Detect current branch; fail on detached HEAD. symbolic-ref exits non-zero on
# detached HEAD, which throws under the native error preference — wrap it to
# convert the throw into a clearer message.
try {
    $source = git symbolic-ref --short HEAD 2>$null
}
catch {
    throw "Not on a branch (detached HEAD?)"
}

if ($source -eq 'main') {
    throw "Already on main; switch to the source branch first."
}

Write-Info "Original branch" $source
Write-Info "Target branch" "main"
if ($useBump) {
    Write-Info "Version change" "bump '$Bump'"
}
else {
    Write-Info "Version change" "set to '$Version'"
}

# --- working tree status ---------------------------------------------------

Write-Section "Working tree status"
Write-Run "git status --short --branch"
git status --short --branch

$treeClean = $true
try {
    git diff-index --quiet HEAD --
}
catch {
    $treeClean = $false
}
if ($treeClean) {
    Write-Host "  Working tree is clean." -ForegroundColor Green
}
else {
    throw "Working tree is not clean; commit or stash changes first."
}

# --- versions --------------------------------------------------------------

Write-Section "Versions"
$currentVersion = (uv version --short).Trim()
if ([string]::IsNullOrWhiteSpace($currentVersion)) {
    throw "Failed to read current version from uv."
}
Write-Info "Current version" $currentVersion

Write-Host "  Preview of the version change:" -ForegroundColor DarkGray
if ($useBump) {
    Write-Run "uv version --dry-run --bump $Bump"
    uv version --dry-run --bump $Bump
}
else {
    Write-Run "uv version --dry-run $Version"
    uv version --dry-run $Version
}

# --- release steps ---------------------------------------------------------

Write-Section "Step: switch to main"
Invoke-Step "Switch from '$source' to 'main'?" {
    Write-Run "git switch main"
    git switch main
}

Write-Section "Step: merge '$source' into main"
Invoke-Step "Merge '$source' into 'main'?" {
    Write-Run "git merge $source"
    git merge $source
}

Write-Section "Step: update version"
Invoke-Step "Apply the version change?" {
    if ($useBump) {
        Write-Run "uv version --bump $Bump"
        uv version --bump $Bump
    }
    else {
        Write-Run "uv version $Version"
        uv version $Version
    }
}

# Brief pause before reading back the version: writing pyproject.toml can leave
# uv.exe momentarily busy on Windows, which causes errors on the next call.
Start-Sleep -Seconds 1

$version = (uv version --short).Trim()
if ([string]::IsNullOrWhiteSpace($version)) {
    throw "Failed to read new version from uv after update."
}
Write-Info "New version" $version

Write-Section "Step: commit release"
Invoke-Step "Stage all changes and commit as 'Release v$version'?" {
    Write-Run "git add ."
    git add .
    Write-Run "git commit -m `"Release v$version`""
    git commit -m "Release v$version"
}

Write-Section "Step: tag release"
Invoke-Step "Create annotated tag 'v$version'?" {
    Write-Run "git tag -a `"v$version`" -m `"Release $version`""
    git tag -a "v$version" -m "Release $version"
}

Write-Section "Step: push main"
Invoke-Step "Push 'main' to origin?" {
    Write-Run "git push origin main"
    git push origin main
}

Write-Section "Step: push tags"
Invoke-Step "Push tags to origin?" {
    Write-Run "git push origin --tags"
    git push origin --tags
}

Write-Section "Step: return to '$source'"
Invoke-Step "Switch back to '$source'?" {
    Write-Run "git switch $source"
    git switch $source
}

Write-Section "Step: merge main into '$source'"
Invoke-Step "Merge 'main' into '$source'?" {
    Write-Run "git merge main"
    git merge main
}

Write-Section "Step: push '$source'"
Invoke-Step "Push '$source' to origin?" {
    Write-Run "git push origin $source"
    git push origin $source
}

# --- done ------------------------------------------------------------------

Write-Section "Done"
Write-Host "  Released v$version." -ForegroundColor Green
Write-Info "Current branch" (git branch --show-current)
