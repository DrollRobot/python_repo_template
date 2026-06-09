#Requires -Version 7.4

<#
.SYNOPSIS
    Merge the current branch into main, bump the version, tag, and push.

.DESCRIPTION
    Detects the current git branch, merges it into main, bumps the project
    version via uv, commits and tags the release, pushes main and tags to
    origin, then merges main back into the source branch and pushes it.

.PARAMETER Bump
    Semantic version bump level. One of:
      patch — bug fixes only           (1.4.2 -> 1.4.3)
      minor — new features, no breaks  (1.4.2 -> 1.5.0)
      major — breaking changes         (1.4.2 -> 2.0.0)

.EXAMPLE
    .\release.ps1 patch

.EXAMPLE
    .\release.ps1 minor

.NOTES
    Requirements:
      - PowerShell 7.4 or later.
      - Run from inside the source branch with a clean working tree.
      - `uv` installed and the project uses uv for version management.
      - Push access to origin for both main and the source branch.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('patch', 'minor', 'major')]
    [string]$Bump
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

# Detect current branch; fail on detached HEAD.
# symbolic-ref exits non-zero on detached HEAD, which now throws — so wrap it
# to convert the throw into a clearer message.
try {
    $source = git symbolic-ref --short HEAD 2>$null
}
catch {
    throw "Not on a branch (detached HEAD?)"
}

if ($source -eq 'main') {
    throw "Already on main; switch to the source branch first"
}

# Clean working tree check. diff-index exits 1 when the tree is dirty, which
# now throws — same pattern: catch and rethrow with a useful message.
try {
    git diff-index --quiet HEAD --
}
catch {
    throw "Working tree is not clean; commit or stash changes first"
}

git switch main
git merge $source

uv version --bump $Bump

Start-Sleep -Seconds 1

$version = (uv version --short).Trim()
if ([string]::IsNullOrWhiteSpace($version)) {
    throw "Failed to read version from uv"
}

git add .
git commit -m "Release v$version"
git tag -a "v$version" -m "Release $version"
git push origin main
git push origin --tags

git switch $source
git merge main
git push origin $source