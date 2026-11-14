# Keep Python and caches local to this checkout. Forwards all arguments to uv.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$env:UV_PYTHON_INSTALL_DIR = Join-Path $repoRoot '.tools\python'
$env:UV_CACHE_DIR = Join-Path $repoRoot '.cache\uv'
$uvBinary = Join-Path $repoRoot '.tools\uv\uv.exe'
if (!(Test-Path -LiteralPath $uvBinary)) {
    throw 'Run .\scripts\bootstrap.ps1 first, or use an installed uv command.'
}
Push-Location $repoRoot
try {
    & $uvBinary @args
    $result = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $result
