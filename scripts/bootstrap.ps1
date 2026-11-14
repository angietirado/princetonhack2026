$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $uvBinary = Join-Path $repoRoot '.tools\uv\uv.exe'
    if (!(Test-Path -LiteralPath $uvBinary)) {
        New-Item -ItemType Directory -Force -Path '.tools' | Out-Null
        Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/astral-sh/uv/releases/download/0.13.8/uv-x86_64-pc-windows-msvc.zip' -OutFile '.tools\uv.zip'
        Expand-Archive -LiteralPath '.tools\uv.zip' -DestinationPath '.tools\uv' -Force
    }
    & .\scripts\uv.ps1 sync --all-packages --locked
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    if (!(Test-Path -LiteralPath '.env')) {
        Copy-Item -LiteralPath '.env.example' -Destination '.env'
    }
    if (!(Test-Path -LiteralPath '.data\demo.json')) {
        & .\scripts\uv.ps1 run --package spending-data-generator generate-data
        if ($LASTEXITCODE -ne 0) { throw 'Fixture generation failed.' }
    }
    Push-Location (Join-Path $repoRoot 'apps\frontend')
    try {
        npm.cmd ci --cache (Join-Path $repoRoot '.cache\npm')
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    } finally {
        Pop-Location
    }
    Write-Output 'Ready. See README.md for the two development server commands.'
} finally {
    Pop-Location
}
