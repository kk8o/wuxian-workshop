<#
.SYNOPSIS
  Packs dist\wuxian\ (scripts\build.ps1) into a Velopack release in dist\releases\: the Setup exe, a portable zip, the
  full package (and a delta against the previous one when it is still there), releases.<channel>.json, and latest.json
  for the website's download page (scripts\release_manifest.py).
.DESCRIPTION
  Runs `vpk pack` with packId WuxianWorkshop.App (Velopack installs into %LOCALAPPDATA%\WuxianWorkshop.App\, apart from
  the program's own files in %LOCALAPPDATA%\WuxianWorkshop\, which an uninstall must not take along), the version from
  pyproject.toml as SemVer (0.8.0.dev0 -> 0.8.0-dev.0) unless -Version says otherwise, packDir dist\wuxian, mainExe
  wuxian.exe, the logo's icon and, with -Notes, a Markdown file of release notes (shown in the app before updating).
  vpk is a .NET SDK global tool (dotnet tool install -g vpk); when it is missing this script says so and exits with
  code 3. Uploading the result to wuxianwow.com/workshop/releases is a separate step.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\pack.ps1 [-Channel win] [-Version 0.9.0] [-Notes notes\0.9.0.md]
#>
param(
    [string]$Channel = "win",
    [string]$Version = "",
    [string]$Notes = "",
    [string]$OutDir = ""
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$PackDir = Join-Path $Root "dist\wuxian"
if (-not $OutDir) { $OutDir = Join-Path $Root "dist\releases" }
$Icon = Join-Path $Root "src\wuxianworkshop\ui\static\wuxian.ico"
if (-not (Test-Path (Join-Path $PackDir "wuxian.exe"))) {
    Write-Host "dist\wuxian\wuxian.exe not found: run scripts\build.ps1 first"
    exit 2
}
$vpk = Get-Command vpk -ErrorAction SilentlyContinue
if (-not $vpk) {
    Write-Host "vpk (the Velopack packer) is not installed. It is a .NET SDK global tool:"
    $dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
    if ($dotnet) {
        Write-Host ("  .NET SDK found (" + (& dotnet --version) + "); install the tool with:  dotnet tool install -g vpk")
    }
    else {
        Write-Host "  install the .NET SDK first (https://dotnet.microsoft.com/download), then:  dotnet tool install -g vpk"
    }
    Write-Host "This script installs nothing itself; run it again once vpk is on PATH."
    exit 3
}
if (-not $Version) {
    $Version = & $Py (Join-Path $Root "scripts\version_info.py") --semver
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
Write-Host "packing WuxianWorkshop.App $Version from $PackDir into $OutDir (channel $Channel)"
# --skipVeloAppCheck: vpk looks for the Velopack SDK inside the main exe; ours is in the velopack module under _internal,
# and scripts\wuxian_entry.py runs velopack.App().run() before anything else.
$vpkArgs = @("pack", "--packId", "WuxianWorkshop.App", "--packVersion", $Version, "--packDir", $PackDir, "--mainExe", "wuxian.exe",
          "--outputDir", $OutDir, "--channel", $Channel, "--packTitle", "无限工坊", "--packAuthors", "Wuxian Workshop",
          "--icon", $Icon, "--skipVeloAppCheck")
if ($Notes) { $vpkArgs += @("--releaseNotes", (Resolve-Path $Notes).Path) }
& vpk @vpkArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Py (Join-Path $Root "scripts\release_manifest.py") --channel $Channel --dir $OutDir
exit $LASTEXITCODE
