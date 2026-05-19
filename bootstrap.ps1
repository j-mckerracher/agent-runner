[CmdletBinding()]
param (
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgsList = @()
)

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$BootstrapScript = Join-Path $RootDir "bootstrap.py"

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $BootstrapScript @ArgsList
    exit $LASTEXITCODE
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    & python $BootstrapScript @ArgsList
    exit $LASTEXITCODE
}

Write-Error "bootstrap.ps1 requires Python 3 on PATH."
exit 1
