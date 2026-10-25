# Baseline part C's scan-scope check (baseline_run_list.md): the firmware repo's
# scan_scope_check.py, from an IDF-exported shell. C5 (stale_name_check.py) and C6
# (name_once_check.py) were retired at port step 0c with their spec 12.4 rows (user,
# 2026-10-25). It runs idf.py itself and cannot be
# throttled, so run this only when no wall-clock test of either project is running
# (firmware/esp-development.md). Gen-inhibit tester, 2026-10-17 (run by hand on
# c65c45e; this records it).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File c_checks_run.ps1 -Clone <tester clone> -Commit <sha> -Components <verified managed_components> -OutDir <runs dir>
#
# Writes <OutDir>/<check>_<commit>.{json,log,txt}; .txt is stdout plus a start and
# an end line (time, exit code). -Components must be a tree whose prod build
# verified it against CHECKSUMS.json and the lock (12.3 item 3).
param(
    [Parameter(Mandatory = $true)][string]$Clone,
    [Parameter(Mandatory = $true)][string]$Commit,
    [Parameter(Mandatory = $true)][string]$Components,
    [Parameter(Mandatory = $true)][string]$OutDir
)
$ErrorActionPreference = "Continue"
if (-not $env:IDF_PATH) { throw "set IDF_PATH to the ESP-IDF tree (tools-reference.md)" }
if (-not $env:GI_IDF_PYTHON) { throw "set GI_IDF_PYTHON to the 32-bit Python 3.11 folder export.ps1 needs (tools-reference.md)" }
$env:PATH = "$env:GI_IDF_PYTHON;" + $env:PATH
# Started from Git Bash (the chain runner), the shell inherits MSYSTEM, and IDF's export
# refuses any shell that has it ("MSys/Mingw is not supported", 2026-10-19).
Remove-Item Env:MSYSTEM -ErrorAction SilentlyContinue
. "$env:IDF_PATH\export.ps1" | Out-Null
New-Item -ItemType Directory -Force $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path
$checks = Join-Path $Clone "test\build_checks"
foreach ($c in @("scan_scope_check")) {
    $txt = Join-Path $OutDir "$($c)_$Commit.txt"
    $json = Join-Path $OutDir "$($c)_$Commit.json"
    "start $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $c --commit $Commit (clone HEAD $(& git -C $Clone rev-parse --short HEAD))" |
        Out-File -Encoding utf8 $txt
    Push-Location $checks
    & python -B "$c.py" --commit $Commit --components $Components --out $json *>&1 |
        ForEach-Object { "$_" } | Out-File -Encoding utf8 -Append $txt
    $code = $LASTEXITCODE
    Pop-Location
    "end $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') exit $code" | Out-File -Encoding utf8 -Append $txt
    "$c exit $code"
}
"c checks done"
