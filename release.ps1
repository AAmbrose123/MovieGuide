# Publish a new Movie Guide version. GitHub builds MovieGuide.exe and every installed copy offers the update.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$Repo = "https://github.com/AAmbrose123/MovieGuide.git"

function Fail($msg) {
    Write-Host ""
    Write-Host $msg -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}
function Run {
    & git @args
    if ($LASTEXITCODE -ne 0) { Fail "This step failed:  git $($args -join ' ')" }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail "Git is not installed. Get it from https://git-scm.com/download/win"
}

# First run: link this folder to the GitHub repo (your files are kept as they are)
if (-not (Test-Path ".git")) {
    Write-Host "Linking this folder to $Repo ..."
    Run init -q
    Run remote add origin $Repo
}
if (-not (git remote)) { Run remote add origin $Repo }
Run fetch -q origin
if (-not (git rev-parse -q --verify HEAD 2>$null)) {
    Run reset -q origin/main
}
git branch -M main 2>$null
git branch -q -u origin/main 2>$null
if (-not (git config user.name))  { Run config user.name "AAmbrose123" }
if (-not (git config user.email)) { Run config user.email "AAmbrose123@users.noreply.github.com" }

# Put the GitHub build workflow in place (it ships as release-workflow.yml)
if (Test-Path "release-workflow.yml") {
    New-Item -ItemType Directory -Force ".github\workflows" | Out-Null
    Move-Item -Force "release-workflow.yml" ".github\workflows\release.yml"
}

# Catch up with anything pushed to GitHub from elsewhere (Claude keeps this folder's files in step,
# so only the history needs to move - your files are left exactly as they are)
Run fetch -q origin
git merge-base --is-ancestor HEAD origin/main 2>$null
if ($LASTEXITCODE -eq 0) { Run reset -q origin/main }
else { Run pull -q --rebase --autostash origin main }

$py = Join-Path $PSScriptRoot "movie_guide.py"
$utf8 = New-Object System.Text.UTF8Encoding $false
$src = [IO.File]::ReadAllText($py, $utf8)
$cur = if ($src -cmatch '(?m)^VERSION = "([^"]*)"') { $Matches[1] } else { "?" }

Write-Host ""
Write-Host "Current version: $cur" -ForegroundColor Cyan
$ver = (Read-Host "New version number (e.g. 1.0.1)").Trim().TrimStart("v", "V")
if (-not $ver) { Fail "Cancelled." }
if ($ver -notmatch '^\d+(\.\d+){1,3}$') { Fail "'$ver' isn't a version number like 1.0.1" }
if (git tag -l "v$ver") { Fail "v$ver was already released - pick a higher number." }
$msg = (Read-Host "What changed? (one line)").Trim()
if (-not $msg) { $msg = "Version $ver" }

# only the real VERSION line (case-sensitive, start of line) - nothing else in the file
$src = ([regex]'(?m)^VERSION = "[^"]*"').Replace($src, "VERSION = `"$ver`"", 1)
[IO.File]::WriteAllText($py, $src, $utf8)

Run add -A
Run commit -q -m "v${ver}: $msg"
Run tag -a "v$ver" -m $msg
Write-Host "Uploading to GitHub (a sign-in window may pop up the first time)..."
Run push -q -u origin main
Run push -q origin "v$ver"

Write-Host ""
Write-Host "Done! GitHub is building v$ver now (about 3-5 minutes)." -ForegroundColor Green
Write-Host "The download will appear at https://github.com/AAmbrose123/MovieGuide/releases"
Start-Process "https://github.com/AAmbrose123/MovieGuide/actions"
Read-Host "Press Enter to close"
