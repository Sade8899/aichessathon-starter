[CmdletBinding(PositionalBinding = $false)]
param(
    [switch]$Build,
    [switch]$Package,
    [switch]$Detached,
    [string]$CpuSet = '',
    [string]$LogName = 'chessathon-test',
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)][string[]]$Command
)
$ErrorActionPreference = 'Stop'
$imageName = 'chessathon-scope:test'
$workspacePath = $PSScriptRoot
if ($Build) {
    docker build --target python-test -f "$workspacePath/Dockerfile.test" -t $imageName $workspacePath *> "$env:TEMP/chessathon-build.log"
    if ($LASTEXITCODE -ne 0) { Get-Content "$env:TEMP/chessathon-build.log" -Tail 80; exit $LASTEXITCODE }
}
$dockerArgs = @('run', '--network', 'none', '--cpus', '1', '--memory', '2g',
    '--pids-limit', '128', '--read-only', '--tmpfs', '/tmp:rw,size=256m',
    '--mount', "type=bind,source=$workspacePath,target=/workspace,readonly")
if ($CpuSet) { $dockerArgs += @('--cpuset-cpus', $CpuSet) }
if ($Detached) {
    $dockerArgs += @('--detach', '--name', $LogName)
} else {
    $dockerArgs += '--rm'
}
if ($Package) {
    $outputPath = Join-Path $workspacePath 'artifacts'
    New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
    $dockerArgs += @('--mount', "type=bind,source=$outputPath,target=/output")
}
if (-not $Command) { $Command = @('make', 'gate') }
$logPath = Join-Path $env:TEMP "$LogName.log"
& docker @dockerArgs $imageName @Command *> $logPath
$resultCode = $LASTEXITCODE
Get-Content $logPath -Tail 80
Write-Output "Log: $logPath"
exit $resultCode
