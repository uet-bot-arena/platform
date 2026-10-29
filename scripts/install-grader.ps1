# Run from a verified checkout; docs/thiet-ke.md describes the SHA-256 check.
[CmdletBinding()]
param(
    [string]$HubUrl,
    [ValidateRange(1,64)][int]$Slots = 1,
    [ValidatePattern('^[a-zA-Z0-9_-]{1,40}$')][string]$Profile = 'default',
    [string]$Python = 'python',
    [switch]$Stop,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$arenaBase = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'uet-arena'))
$arenaInstall = [IO.Path]::GetFullPath((Join-Path $arenaBase "grader-install-$Profile"))
if (-not $arenaInstall.StartsWith($arenaBase + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Install path is outside the grader directory.'
}
$arenaStartup = Join-Path ([Environment]::GetFolderPath('Startup')) "UET Arena grader $Profile.lnk"
$arenaStopFile = Join-Path $arenaInstall 'stop'
$arenaPidFile = Join-Path $arenaInstall 'grader.pid'
if ($Stop -or $Uninstall) {
    if (Test-Path -LiteralPath $arenaInstall) {
        [IO.File]::WriteAllText($arenaStopFile, '')
        if (Test-Path -LiteralPath $arenaPidFile) {
            $arenaPid = [int](Get-Content -LiteralPath $arenaPidFile -Raw)
            $arenaUntil = [DateTime]::UtcNow.AddSeconds(40)
            do {
                $arenaProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$arenaPid"
                # Store Python launches the real process from WindowsApps, outside the venv.
                $arenaRunning = $arenaProcess -and $arenaProcess.CommandLine -and
                    $arenaProcess.CommandLine.Contains('-m arena.grader') -and $arenaProcess.CommandLine.Contains($arenaPidFile)
                if (-not $arenaRunning) { break }
                Start-Sleep -Milliseconds 250
            } while ([DateTime]::UtcNow -lt $arenaUntil)
            if ($arenaRunning) { throw 'Grader has not stopped. Inspect grader.log before uninstalling.' }
        }
    }
    if ($Uninstall) {
        if (Test-Path -LiteralPath $arenaStartup) { Remove-Item -LiteralPath $arenaStartup }
        if (Test-Path -LiteralPath $arenaInstall) { Remove-Item -LiteralPath $arenaInstall -Recurse -Force }
        Write-Output 'Grader removed; shared toolchain and downloads were kept.'
    } else { Write-Output 'Grader stopped and its leases returned.' }
    return
}
if (-not $HubUrl -or $HubUrl -notmatch '^https?://[^\s"@?#]+$') { throw 'Supply -HubUrl http(s)://host[:port].' }
$arenaRepo = Split-Path -Parent $PSScriptRoot
$arenaSource = Join-Path $arenaRepo 'arena-backend'
$arenaApp = Join-Path $arenaInstall 'app'
$arenaVenv = Join-Path $arenaInstall 'venv'
if (Test-Path -LiteralPath $arenaPidFile) {
    throw 'Existing installation: run this script with -Stop, then remove grader.pid before reinstalling.'
}
New-Item -ItemType Directory -Path $arenaApp -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $arenaSource 'arena') -Destination $arenaApp -Recurse -Force
Copy-Item -LiteralPath (Join-Path $arenaSource 'sdk') -Destination $arenaApp -Recurse -Force
& $Python -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
& $Python -m venv --without-pip $arenaVenv
if ($LASTEXITCODE -ne 0) { throw 'Could not create venv.' }
$arenaExe = Join-Path $arenaVenv 'Scripts\python.exe'
if (Test-Path -LiteralPath $arenaStopFile) { Remove-Item -LiteralPath $arenaStopFile }
Push-Location $arenaApp
try {
    & $arenaExe -m arena.sandbox.toolchain install
    if ($LASTEXITCODE -ne 0) { throw 'Toolchain installation failed.' }
    & $arenaExe -m arena.grader --hub $HubUrl --backend windows --slots $Slots --profile $Profile `
        --token-file (Join-Path $arenaInstall 'grader-token') --check
    if ($LASTEXITCODE -ne 0) { throw 'Grader check failed; no Startup shortcut was created.' }
} finally { Pop-Location }
$arenaShell = New-Object -ComObject WScript.Shell
$arenaShortcut = $arenaShell.CreateShortcut($arenaStartup)
$arenaShortcut.TargetPath = Join-Path $arenaVenv 'Scripts\pythonw.exe'
$arenaShortcut.WorkingDirectory = $arenaApp
$arenaShortcut.Arguments = '-m arena.grader --backend windows --hub "{0}" --slots {1} --profile "{2}" --token-file "{3}" --state-dir "{4}" --log-file "{5}" --pid-file "{6}" --stop-file "{7}"' -f `
    $HubUrl, $Slots, $Profile, (Join-Path $arenaInstall 'grader-token'), (Join-Path $arenaInstall 'state'), `
    (Join-Path $arenaInstall 'grader.log'), $arenaPidFile, $arenaStopFile
$arenaShortcut.Save()
Start-Process -FilePath $arenaShortcut.TargetPath -ArgumentList $arenaShortcut.Arguments -WorkingDirectory $arenaApp -WindowStyle Hidden
Write-Output "Grader installed. Startup: $arenaStartup"
Write-Output "Log: $(Join-Path $arenaInstall 'grader.log')"
