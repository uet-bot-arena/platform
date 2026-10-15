param(
    [ValidateRange(1024, 65535)][int]$Port = 41810,
    [ValidatePattern('^[A-Za-z0-9._-]+$')][string]$WslDistro = 'Ubuntu'
)
$ErrorActionPreference = 'Stop'
$arenaOrigin = "http://127.0.0.1:$Port"
try {
    $arenaLive = Invoke-RestMethod -Uri "$arenaOrigin/api/v1/health/live" -TimeoutSec 2
    Write-Output "Backend already serves $arenaOrigin"
    return
} catch { }
$arenaRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$arenaScript = Join-Path $PSScriptRoot 'serve-local.sh'
$arenaWslScript = '/mnt/' + $arenaScript.Substring(0,1).ToLowerInvariant() + $arenaScript.Substring(2).Replace('\','/')
$arenaLogs = Join-Path $arenaRoot '.platform-state\logs'
New-Item -ItemType Directory -Path $arenaLogs -Force | Out-Null
$arenaStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$arenaOut = Join-Path $arenaLogs "api-$Port-$arenaStamp.out.log"
$arenaErr = Join-Path $arenaLogs "api-$Port-$arenaStamp.err.log"
$arenaProcess = Start-Process -FilePath 'wsl.exe' -WindowStyle Hidden -PassThru `
    -ArgumentList @('-d',$WslDistro,'--','bash','--',('"' + $arenaWslScript + '"'),[string]$Port) `
    -RedirectStandardOutput $arenaOut -RedirectStandardError $arenaErr
Write-Output "Started local backend process $($arenaProcess.Id) at $arenaOrigin"
Write-Output "Diagnostics: $arenaErr"
