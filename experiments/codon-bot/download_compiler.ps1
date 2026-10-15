$ErrorActionPreference = 'Stop'
$toolsDirectory = Join-Path $PSScriptRoot '.tools'
New-Item -ItemType Directory -Force -Path $toolsDirectory | Out-Null
$archivePath = Join-Path $toolsDirectory 'codon-linux-x86_64-v0.20.3.tar.gz'
$expectedHash = '8716e5753a441764d13fd6a0b4f911060d292f66c9e61d0a4cdccb64c843da6f'
if (-not (Test-Path -LiteralPath $archivePath)) {
    Invoke-WebRequest -Uri 'https://github.com/exaloop/codon/releases/download/v0.20.3/codon-linux-x86_64.tar.gz' -OutFile $archivePath -TimeoutSec 180
}
$actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) { throw 'Codon release checksum did not match the GitHub release metadata.' }
Write-Output "Verified official Codon v0.20.3 archive: $actualHash"
