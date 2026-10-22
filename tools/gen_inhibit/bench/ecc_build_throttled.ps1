# The B6 measurement image (_ecc, GI_INSTRUMENT_ECC=1), the tester's own clean build of
# one commit, THROTTLED (firmware/esp-development.md, "Builds during wall-clock tests",
# point 3: the shell at Idle priority, ninja -j 1; the build's processes inherit it).
# Gen-inhibit tester, 2026-10-20. Run from Windows PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File ecc_build_throttled.ps1 -Tree <clean worktree> -Log <file>
#
# Logs git HEAD and status before, both commands, their full output, times and exit
# codes. Refuses an existing build-tester-ecc directory (each build must be clean).
# The image is then recorded from the file (baseline_images_record.py), never its name.
param(
    [Parameter(Mandatory=$true)][string]$Tree,
    [Parameter(Mandatory=$true)][string]$Log
)
$Tree = (Resolve-Path $Tree).Path
$dir = Join-Path $Tree "build-tester-ecc"
if (Test-Path $dir) { Write-Output "$dir exists, refused"; exit 2 }
# Absolute, because the build runs from inside $Tree (2026-10-20: a relative -Log
# failed every write after Push-Location, the commands never ran, and the exit was 0).
if (Test-Path $Log) { Write-Output "$Log exists, refused"; exit 2 }
New-Item -ItemType File -Force $Log | Out-Null
$Log = (Resolve-Path $Log).Path
if (-not $env:IDF_PATH) { throw "set IDF_PATH to the ESP-IDF tree (tools-reference.md)" }
if (-not $env:GI_IDF_PYTHON) { throw "set GI_IDF_PYTHON to the 32-bit Python 3.11 folder export.ps1 needs (tools-reference.md)" }
$env:PATH = "$env:GI_IDF_PYTHON;" + $env:PATH
. "$env:IDF_PATH\export.ps1" | Out-Null
(Get-Process -Id $PID).PriorityClass = 'Idle'
$head = (git -C $Tree rev-parse HEAD)
$st = (git -C $Tree status --porcelain) -join "; "
"git status before: [$st] HEAD $head" | Out-File -Encoding utf8 $Log
"priority: $((Get-Process -Id $PID).PriorityClass)" | Out-File -Encoding utf8 -Append $Log
$rc = 0
$steps = @(
    @("idf.py", @("-B", $dir, "-DGI_INSTRUMENT_ECC=1", "reconfigure")),
    @("ninja", @("-C", $dir, "-j", "1"))
)
foreach ($s in $steps) {
    $t0 = Get-Date
    "command: $($s[0]) $($s[1] -join ' ')" | Out-File -Encoding utf8 -Append $Log
    "start: $($t0.ToString('yyyy-MM-dd HH:mm:ss'))" | Out-File -Encoding utf8 -Append $Log
    Push-Location $Tree
    if ($s[0] -eq "idf.py") {
        & python "$env:IDF_PATH\tools\idf.py" @($s[1]) *>&1 | ForEach-Object { "$_" } | Out-File -Encoding utf8 -Append $Log
    } else {
        & ninja @($s[1]) *>&1 | ForEach-Object { "$_" } | Out-File -Encoding utf8 -Append $Log
    }
    $code = $LASTEXITCODE
    Pop-Location
    if ($null -eq $code) { $code = 99 }
    $t1 = Get-Date
    "end: $($t1.ToString('yyyy-MM-dd HH:mm:ss'))  seconds $([int]($t1 - $t0).TotalSeconds)  exit $code" | Out-File -Encoding utf8 -Append $Log
    Write-Output ("{0}: exit {1}, {2} s" -f $s[0], $code, [int]($t1 - $t0).TotalSeconds)
    if ($code -ne 0) { $rc = 1; break }
}
$st2 = (git -C $Tree status --porcelain) -join "; "
"git status after: [$st2]" | Out-File -Encoding utf8 -Append $Log
# A build that produced no image is a failure whatever the exit codes said.
$bins = @(Get-ChildItem -Path $dir -Filter "wican-fw_*.bin" -ErrorAction SilentlyContinue)
"images: $($bins.Count) ($(($bins | ForEach-Object { $_.Name }) -join ', '))" | Out-File -Encoding utf8 -Append $Log
if ($bins.Count -ne 1) { Write-Output "expected one image in $dir, found $($bins.Count)"; $rc = 1 }
exit $rc
