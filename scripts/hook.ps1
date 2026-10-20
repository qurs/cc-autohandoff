$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$binary = Join-Path $PSScriptRoot '../bin/cc-autohandoff.exe'
[Console]::In.ReadToEnd() | & $binary hook
exit $LASTEXITCODE
