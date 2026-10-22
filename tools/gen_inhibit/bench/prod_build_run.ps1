# Run the firmware repo's tools/prod_build.py in a clone on gen-inhibit/main and
# log it: a start line (time, HEAD, branch, git status), the full output, an end
# line with the exit code. Gen-inhibit tester, baseline part C1 (prod image).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File prod_build_run.ps1 -Tree <clone> -Log <file> [-Idle]
#
# -Idle runs it at Idle priority (the builds rule while any wall-clock test of
# either project is running, firmware/esp-development.md). It does not limit
# ninja's jobs: prod_build.py runs idf.py itself and takes no -j.
param(
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
"start $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') HEAD $head branch $branch status [$($status -join '; ')]" |
    Out-File -Encoding utf8 $Log
Push-Location $Tree
& python tools\prod_build.py *>&1 | ForEach-Object { "$_" } | Out-File -Encoding utf8 -Append $Log
$code = $LASTEXITCODE
Pop-Location
"end $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') exit $code" | Out-File -Encoding utf8 -Append $Log
"prod build: exit $code"
