$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))

if (-not (Test-Path -LiteralPath (Join-Path $Root 'grade_alert.py'))) {
    throw 'Safety check failed: grade_alert.py was not found beside the uninstaller.'
}

Write-Host 'This removes the local runtime, browser session, grade snapshots, config, and encrypted credentials.'
Write-Host 'Source code and documentation will remain in this folder.'
$answer = Read-Host 'Type DELETE to continue'
if ($answer -ne 'DELETE') {
    Write-Host 'Cancelled.'
    exit 0
}

$relativeTargets = @(
    '.venv',
    '.browser-profile',
    'data',
    'config.local.json',
    'credentials.dat',
    '__pycache__',
    'tests\__pycache__'
)

foreach ($relative in $relativeTargets) {
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $Root $relative))
    if (-not $candidate.StartsWith($Root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Safety check failed for target: $candidate"
    }
    if (Test-Path -LiteralPath $candidate) {
        Remove-Item -LiteralPath $candidate -Recurse -Force
        Write-Host "Removed: $relative"
    }
}

Write-Host 'Local uninstall completed.'
