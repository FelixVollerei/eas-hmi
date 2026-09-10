param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$archiveRoot = Join-Path (Split-Path -Parent $projectRoot) 'eas-hmi-local-artifacts'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$destinationRoot = Join-Path $archiveRoot ('archived-' + $stamp)
$records = @()
foreach ($relative in @('build/environments', 'build/clean-checkouts')) {
    $candidate = Join-Path $projectRoot $relative
    if (-not (Test-Path -LiteralPath $candidate)) { continue }
    $source = (Resolve-Path -LiteralPath $candidate).Path
    if (-not $source.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Source outside project: $source"
    }
    $destination = [IO.Path]::GetFullPath((Join-Path $destinationRoot (Split-Path -Leaf $source)))
    if (-not $destination.StartsWith([IO.Path]::GetFullPath($archiveRoot) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Destination outside archive: $destination"
    }
    $size = (Get-ChildItem -LiteralPath $source -File -Recurse | Measure-Object Length -Sum).Sum
    $records += [pscustomobject]@{source=$source; destination=$destination; bytes=$size; applied=[bool]$Apply}
    if ($Apply) {
        New-Item -ItemType Directory -Path $destinationRoot -Force | Out-Null
        Move-Item -LiteralPath $source -Destination $destination
    }
}
if ($Apply) {
    $records | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destinationRoot 'archive-manifest.json') -Encoding utf8
}
$records | ConvertTo-Json
