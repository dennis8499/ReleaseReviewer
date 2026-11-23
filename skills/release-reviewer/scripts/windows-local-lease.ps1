param([switch]$Run, [switch]$StartMonitor, [switch]$Terminal, [string]$RequestPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)

try {
    $sources = @(
        (Join-Path $PSScriptRoot 'repair-workflow-lease-native.cs')
        (Join-Path $PSScriptRoot 'windows-local-lease.cs')
        (Join-Path $PSScriptRoot 'windows-lease-process.cs')
    )
    Add-Type -Path $sources -ReferencedAssemblies @('System.Web.Extensions', 'System.Core') -ErrorAction Stop
    if ($StartMonitor) { exit [WindowsLocalLease]::StartMonitor((Join-Path $PSHOME 'powershell.exe'), $PSCommandPath) }
    if ($Run) { exit [WindowsLocalLease]::Run([Console]::In.ReadLine(), $false) }
    if ($Terminal) {
        $parent = [WorkflowLeaseDirectory]::Open([IO.Path]::GetDirectoryName($RequestPath), $false)
        try {
            $launch = [Text.Encoding]::UTF8.GetString([WorkflowLeaseDirectory]::ReadDocument($RequestPath, 1024 * 1024))
        } finally { $parent.Dispose() }
        exit [WindowsLocalLease]::Run($launch, $true)
    }
    [WindowsLocalLease]::Serve()
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 125
}
