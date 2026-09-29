[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CredentialPath
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

if (-not (Test-Path -LiteralPath $CredentialPath -PathType Leaf)) {
    exit 2
}

$encrypted = [System.IO.File]::ReadAllText($CredentialPath, [System.Text.Encoding]::UTF8).Trim()
if ([string]::IsNullOrWhiteSpace($encrypted)) {
    exit 3
}

$secure = ConvertTo-SecureString $encrypted
$pointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    [Console]::Out.Write([System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer))
} finally {
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    $secure.Dispose()
}
