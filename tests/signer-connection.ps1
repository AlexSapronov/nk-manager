# Windows-only protocol test: mocks certificate store and HTTP; never calls CAdES.
$ErrorActionPreference = 'Stop'
$signer = Join-Path (Split-Path $PSScriptRoot -Parent) 'signer\Start-Signer.ps1'
Add-Type @'
using System;
public sealed class SignerTestResponse { public int StatusCode { get; set; } }
public sealed class SignerTestHttpException : Exception {
    public SignerTestResponse Response { get; private set; }
    public SignerTestHttpException(int code) : base("do-not-echo-test-secret") {
        Response = new SignerTestResponse { StatusCode = code };
    }
}
'@
$global:nkSignerTestCode = 200
$global:nkSignerTestCalls = 0
function global:Get-ChildItem {
    param([string]$Path)
    if ($Path -ne 'Cert:\CurrentUser\My') { throw 'Unexpected certificate query' }
    [pscustomobject]@{ Thumbprint=('A' * 40); HasPrivateKey=$true; NotBefore=(Get-Date).AddDays(-1); NotAfter=(Get-Date).AddDays(30) }
}
function global:Read-Host {
    param([string]$Prompt, [switch]$AsSecureString)
    ConvertTo-SecureString 'test-only-token-not-for-output' -AsPlainText -Force
}
function global:Invoke-RestMethod {
    param([string]$Method,[string]$Uri,[hashtable]$Headers,[string]$ContentType,[byte[]]$Body)
    $global:nkSignerTestCalls++
    # CheckConnection must only heartbeat, without taking a lease or signing.
    if ($Uri -ne 'http://localhost:8020/api/signer/heartbeat') { throw 'Unexpected endpoint' }
    if ($Headers.Authorization -ne 'Bearer test-only-token-not-for-output') { throw 'Token not passed correctly' }
    $data = [Text.Encoding]::UTF8.GetString($Body) | ConvertFrom-Json
    if ($data.thumbprint -ne ('A' * 40)) { throw 'Unexpected thumbprint' }
    if ($global:nkSignerTestCode -eq 0) {
        throw [Net.WebException]::new('do-not-echo-test-secret',[Net.WebExceptionStatus]::ConnectFailure)
    }
    if ($global:nkSignerTestCode -ne 200) {
        throw [SignerTestHttpException]::new($global:nkSignerTestCode)
    }
    [pscustomobject]@{ok=$true}
}
try {
    foreach ($code in @(200,403,422,404,500,0)) {
        $global:nkSignerTestCode = $code
        $global:nkSignerTestCalls = 0
        $failure = $null
        try { & $signer -Server http://localhost:8020 -Thumbprint ('A' * 40) -CheckConnection }
        catch { $failure = $_.Exception.Message }
        if ($global:nkSignerTestCalls -ne 1) { throw 'Connection check sent more than one request' }
        if ($code -eq 200) {
            if ($failure) { throw ('Successful heartbeat failed: ' + $failure) }
        } else {
            if (-not $failure) { throw 'Failed heartbeat was reported as success' }
            if ($failure -match 'test-secret|test-only-token') { throw 'Secret leaked into diagnostics' }
            if ($code -and $failure -notmatch ('HTTP ' + $code + ' at heartbeat')) { throw 'Missing HTTP status or stage' }
            if ($code -eq 0 -and $failure -notmatch 'no usable HTTP response') { throw 'Missing connection failure hint' }
        }
    }
    Write-Host 'Signer connection protocol: OK (200/403/422/404/500/connection failure; no CAdES or NK calls).'
} finally {
    Remove-Item Function:\Get-ChildItem,Function:\Read-Host,Function:\Invoke-RestMethod
    Remove-Variable nkSignerTestCode,nkSignerTestCalls -Scope Global
}
