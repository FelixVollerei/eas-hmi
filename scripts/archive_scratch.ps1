param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$archiveRoot = Join-Path (Split-Path -Parent $projectRoot) 'eas-hmi-local-artifacts'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$destinationRoot = Join-Path $archiveRoot ('archived-' + $stamp)
$records = @()
$relativePaths = @('build/environments', 'build/clean-checkouts', '.pytest_cache', '.ruff_cache', '.coverage', 'src/eas_hmi.egg-info', 'build/lib', 'build/bdist.win-amd64')
foreach ($authoredRoot in @('src','tests','scripts')) {
    $relativePaths += Get-ChildItem -LiteralPath (Join-Path $projectRoot $authoredRoot) -Directory -Recurse |
        Where-Object { $_.Name -eq '__pycache__' } |
        ForEach-Object { [IO.Path]::GetRelativePath($projectRoot, $_.FullName) }
}
foreach ($relative in $relativePaths) {
    $candidate = Join-Path $projectRoot $relative
    if (-not (Test-Path -LiteralPath $candidate)) { continue }
    $source = (Resolve-Path -LiteralPath $candidate).Path
    if (-not $source.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Source outside project: $source"
    }
    if ((Get-Item -LiteralPath $source).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Refusing reparse point: $source"
    }
    $destination = [IO.Path]::GetFullPath((Join-Path $destinationRoot $relative))
    if (-not $destination.StartsWith([IO.Path]::GetFullPath($archiveRoot) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Destination outside archive: $destination"
    }
    $size = (Get-ChildItem -LiteralPath $source -File -Recurse | Measure-Object Length -Sum).Sum
    $records += [pscustomobject]@{source=$source; destination=$destination; bytes=$size; applied=[bool]$Apply}
    if ($Apply) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Move-Item -LiteralPath $source -Destination $destination
    }
}
if ($Apply) {
    $records | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destinationRoot 'archive-manifest.json') -Encoding utf8
}
$records | ConvertTo-Json
