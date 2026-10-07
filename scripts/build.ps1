<#
.SYNOPSIS
  Builds dist\wuxian\ (PyInstaller onedir: wuxian.exe + _internal\) from the repository root.
.DESCRIPTION
  Regenerates scripts\version_info.txt from pyproject.toml, runs PyInstaller with scripts\wuxian.spec and prints the time
  it took, the size of dist\wuxian and `wuxian.exe --version`. Needs the virtual environment .venv with the [build] extras
  (pyinstaller, pyinstaller-hooks-contrib). scripts\pack.ps1 turns the result into a Velopack release.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Clean
#>
param(
    [switch]$Clean          # remove build\wuxian and dist\wuxian first
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "no virtual environment at $Py (see AGENTS.md: uv pip install -e `".[build]`")"
    exit 2
}
Push-Location $Root
try {
    if ($Clean) {
        foreach ($d in @("build\wuxian", "dist\wuxian")) {
            if (Test-Path $d) { Remove-Item -Recurse -Force $d }
        }
    }
    $version = & $Py scripts\version_info.py --write
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "building wuxian $version (PyInstaller onedir, console + hide-early, no UPX)"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & $Py -m PyInstaller scripts\wuxian.spec --noconfirm --distpath dist --workpath build --log-level WARN
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $sw.Stop()
    $exe = Join-Path $Root "dist\wuxian\wuxian.exe"
    $files = Get-ChildItem -Recurse -File (Join-Path $Root "dist\wuxian")
    $mb = [math]::Round(($files | Measure-Object Length -Sum).Sum / 1MB, 1)
    Write-Host ("built {0} in {1:N0} s: {2} files, {3} MB" -f $exe, $sw.Elapsed.TotalSeconds, $files.Count, $mb)
    & $exe --version
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
