# Run the firmware repo's test/build_checks/test_prod_build_image_real.py against a
# clone on gen-inhibit/main, with ESP-IDF exported the same way as
# prod_build_run.ps1, and log it: a start line (time, HEAD, branch, git status),
# the full output, an end line with the exit code. Gen-inhibit tester, 2026-10-21
# (the check of 95aa77a: which image prod_build.py judges and records).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File prod_image_real_run.ps1 -Harness <test_prod_build_image_real.py> -Tree <clone> -Log <file> [-Idle]
#
# -Idle runs it at Idle priority (the builds rule while any wall-clock test of
# either project is running, firmware/esp-development.md).
param(
    [Parameter(Mandatory = $true)][string]$Harness,
    [Parameter(Mandatory = $true)][string]$Tree,
    [Parameter(Mandatory = $true)][string]$Log,
    [switch]$Idle
)
$ErrorActionPreference = "Continue"
if (-not $env:IDF_PATH) { throw "set IDF_PATH to the ESP-IDF tree (tools-reference.md)" }
if (-not $env:GI_IDF_PYTHON) { throw "set GI_IDF_PYTHON to the 32-bit Python 3.11 folder export.ps1 needs (tools-reference.md)" }
$env:PATH = "$env:GI_IDF_PYTHON;" + $env:PATH
. "$env:IDF_PATH\export.ps1" | Out-Null
if ($Idle) {
    (Get-Process -Id $PID).PriorityClass = "Idle"
}
New-Item -ItemType File -Force $Log | Out-Null
$Log = (Resolve-Path $Log).Path
$head = (& git -C $Tree rev-parse HEAD)
$branch = (& git -C $Tree rev-parse --abbrev-ref HEAD)
$status = @(& git -C $Tree status --porcelain)
"start $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') HEAD $head branch $branch status [$($status -join '; ')] harness sha256 $((Get-FileHash -Algorithm SHA256 $Harness).Hash.ToLower())" |
    Out-File -Encoding utf8 $Log
& python -u $Harness --repo $Tree *>&1 | ForEach-Object { "$_" } | Out-File -Encoding utf8 -Append $Log
$code = $LASTEXITCODE
"end $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') exit $code" | Out-File -Encoding utf8 -Append $Log
"prod image real: exit $code"
