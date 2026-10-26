# Baseline part C builds (baseline_run_list.md): clean builds of one commit, one
# build directory each, in a tree nobody else uses. Run from Windows PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File baseline_builds.ps1 -Tree <worktree> -Out <log dir> -Builds man,rt,rxq
#
# Each build: git status and HEAD before, the idf.py command, its full output,
# start/end times and exit code, to <Out>\build_<name>.txt. A build directory
# that already exists is refused rather than reused (each must be a clean build).
# Names: man, man2 (a second clean build of the same tree, spec 11's control),
# rt, rxq, txabort, abortwin, flt. A prod build is the shared tooling's release build (tools/vtrux_tools.py release), not this.
param(
    [Parameter(Mandatory=$true)][string]$Tree,
    [Parameter(Mandatory=$true)][string]$Out,
    [Parameter(Mandatory=$true)][string[]]$Builds
)

# Under -File a comma list arrives as one string.
$Builds = @($Builds | ForEach-Object { $_ -split "," } | Where-Object { $_ })

$defs = @{
    "man"      = @();
    "man2"     = @();
    "rt"       = @("-DGI_TEST_RESTART_QUIESCE=1");
    "rxq"      = @("-DGI_INSTRUMENT_RXQ=1");
    "txabort"  = @("-DGI_INSTRUMENT_TXABORT=1");
    "abortwin" = @("-DGI_INSTRUMENT_ABORTWIN=1");
    "flt"      = @("-DOTA_HEALTH_FAULT_INJECT=1");
    # Spec 3.2 / 12.3 item 3 (2026-10-16): the stock features, off in this
    # product, each built ON for its refusal row's failing half. AutoPID needs
    # ELM327 and MQTT (wican_features.h), so its pair is fautopid vs fmqttelm.
    "fmqtt"     = @("-DWICAN_FEATURE_MQTT=ON");
    "felm"      = @("-DWICAN_FEATURE_ELM327=ON");
    "fmqttelm"  = @("-DWICAN_FEATURE_MQTT=ON", "-DWICAN_FEATURE_ELM327=ON");
    "fautopid"  = @("-DWICAN_FEATURE_MQTT=ON", "-DWICAN_FEATURE_ELM327=ON", "-DWICAN_FEATURE_AUTOPID=ON");
    "frealdash" = @("-DWICAN_FEATURE_REALDASH=ON");
    "fall"      = @("-DWICAN_FEATURE_MQTT=ON", "-DWICAN_FEATURE_ELM327=ON", "-DWICAN_FEATURE_AUTOPID=ON",
                    "-DWICAN_FEATURE_REALDASH=ON");
}

New-Item -ItemType Directory -Force $Out | Out-Null
$Out = (Resolve-Path $Out).Path
if (-not $env:IDF_PATH) { throw "set IDF_PATH to the ESP-IDF tree (tools-reference.md)" }
if (-not $env:GI_IDF_PYTHON) { throw "set GI_IDF_PYTHON to the 32-bit Python 3.11 folder export.ps1 needs (tools-reference.md)" }
$env:PATH = "$env:GI_IDF_PYTHON;" + $env:PATH
. "$env:IDF_PATH\export.ps1" | Out-Null

New-Item -ItemType Directory -Force $Out | Out-Null
# Absolute, because the build itself runs from inside $Tree.
$Out = (Resolve-Path $Out).Path
$Tree = (Resolve-Path $Tree).Path
$rc = 0
foreach ($b in $Builds) {
    if (-not $defs.ContainsKey($b)) { Write-Output "unknown build $b"; $rc = 2; continue }
    $dir = Join-Path $Tree ("build-tester-" + $b)
    $log = Join-Path $Out ("build_" + $b + ".txt")
    if (Test-Path $dir) { Write-Output "$b : $dir exists, refused"; $rc = 2; continue }
    $head = (git -C $Tree rev-parse HEAD)
    $st = (git -C $Tree status --porcelain) -join "; "
    $cmd = @("-B", $dir) + $defs[$b] + @("build")
    "git status before: [$st] HEAD $head" | Out-File -Encoding utf8 $log
    "command: idf.py $($cmd -join ' ')" | Out-File -Encoding utf8 -Append $log
    $t0 = Get-Date
    "start: $($t0.ToString('yyyy-MM-dd HH:mm:ss'))" | Out-File -Encoding utf8 -Append $log
    Push-Location $Tree
    & python "$env:IDF_PATH\tools\idf.py" @cmd *>&1 | ForEach-Object { "$_" } | Out-File -Encoding utf8 -Append $log
    $code = $LASTEXITCODE
    Pop-Location
    $t1 = Get-Date
    "end: $($t1.ToString('yyyy-MM-dd HH:mm:ss'))  seconds $([int]($t1 - $t0).TotalSeconds)" | Out-File -Encoding utf8 -Append $log
    "idf exit: $code" | Out-File -Encoding utf8 -Append $log
    Write-Output ("{0}: exit {1}, {2} s, {3} to {4}" -f $b, $code, [int]($t1 - $t0).TotalSeconds, $t0.ToString('HH:mm:ss'), $t1.ToString('HH:mm:ss'))
    if ($code -ne 0) { $rc = 1 }
}
exit $rc
