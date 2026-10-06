# Non-secret, read-only policy and exclusive-copy primitives. No top-level I/O.
function Initialize-C2NativePath {
    if ('C2NativePath' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class C2NativePath {
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern SafeFileHandle CreateFile(string path, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern uint GetFinalPathNameByHandle(SafeFileHandle handle, StringBuilder path, uint length, uint flags);
 [DllImport("shell32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern IntPtr CommandLineToArgvW(string line, out int count);
 [DllImport("kernel32.dll")] public static extern IntPtr LocalFree(IntPtr memory);
}
'@
}
function Get-C2PhysicalPath([string]$Path) {
    Initialize-C2NativePath
    $flags = if (Test-Path -LiteralPath $Path -PathType Container) { 0x02000000 } else { 0 }
    $handle = [C2NativePath]::CreateFile($Path,0x80,7,[IntPtr]::Zero,3,$flags,[IntPtr]::Zero)
    try {
        if ($handle.IsInvalid) { throw 'Native-path metadata open failed; no fallback.' }
        $buffer = New-Object Text.StringBuilder(4096)
        $length = [C2NativePath]::GetFinalPathNameByHandle($handle,$buffer,4096,0)
        if ($length -eq 0 -or $length -ge 4096) { throw 'Native-path resolution failed.' }
        $result = $buffer.ToString()
        if ($result.StartsWith('\\?\')) { $result = $result.Substring(4) }
        return $result.TrimEnd('\')
    } finally { $handle.Dispose() }
}
function Assert-C2PhysicalPath([string]$Path) {
    $expected = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (-not [string]::Equals((Get-C2PhysicalPath $Path),$expected,[StringComparison]::OrdinalIgnoreCase)) { throw 'Filesystem view mismatch; no changes allowed.' }
    $item = Get-Item -LiteralPath $Path -Force
    while ($item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse path refused.' }
        $parent = if ($item.PSIsContainer) { $item.Parent } else { $item.Directory }
        $item = $parent
    }
}
function Assert-C2Hash([string]$Path,[string]$Expected) {
    if ($Expected -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Expected) { throw 'Hash verification failed.' }
}
function Assert-C2Manifest($Manifest,[string[]]$Allowed,[string]$PayloadRoot) {
    if ($Manifest.schema -ne 1 -or @($Manifest.files).Count -ne $Allowed.Count) { throw 'Payload count/schema mismatch.' }
    $seen = @{}
    foreach ($entry in $Manifest.files) {
        $relative = [string]$entry.path
        if ($relative -cnotmatch '^[A-Za-z0-9_./-]+$' -or $relative.Contains('..') -or $relative.StartsWith('/') -or $relative -cnotin $Allowed -or $seen.ContainsKey($relative.ToLowerInvariant())) { throw 'Payload path outside exact allowlist.' }
        if ($entry.sha256 -cnotmatch '^[0-9a-f]{64}$' -or [long]$entry.bytes -lt 1) { throw 'Payload metadata invalid.' }
        $seen[$relative.ToLowerInvariant()] = $true
        $file = Join-Path $PayloadRoot $relative.Replace('/','\')
        Assert-C2PhysicalPath $file
        if ((Get-Item -LiteralPath $file).Length -ne [long]$entry.bytes) { throw 'Payload length mismatch.' }
        Assert-C2Hash $file $entry.sha256
    }
}
function Copy-C2Exclusive([string]$Source,[string]$Destination,[string]$Sha256) {
    Assert-C2Hash $Source $Sha256
    $inputStream = [IO.File]::Open($Source,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        $outputStream = [IO.File]::Open($Destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try { $inputStream.CopyTo($outputStream) } finally { $outputStream.Dispose() }
    } finally { $inputStream.Dispose() }
    Assert-C2Hash $Destination $Sha256
}
function Protect-C2NewRoot([string]$Root,[Security.Principal.SecurityIdentifier]$Owner) {
    if (Test-Path -LiteralPath $Root) { throw 'Target collision; existing files/ACL/input are untouched.' }
    New-Item -ItemType Directory -Path $Root -ErrorAction Stop | Out-Null
    $security = New-Object Security.AccessControl.DirectorySecurity
    $security.SetOwner($Owner)
    $security.SetAccessRuleProtection($true,$false)
    $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    foreach ($principal in @($Owner,(New-Object Security.Principal.SecurityIdentifier('S-1-5-18')),(New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))) {
        $security.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($principal,'FullControl',$inherit,'None','Allow')))
    }
    Set-Acl -LiteralPath $Root -AclObject $security
}
function Get-C2StopDecision($Record,$Process,[int[]]$Listeners,[int[]]$Candidates,[string]$OwnerSid) {
    if (@($Candidates).Count -gt 1) { throw 'Multiple controller processes; no replacement.' }
    if (-not $Process) {
        if (@($Listeners).Count -or @($Candidates).Count) { throw 'Unowned listener/process; no replacement.' }
        return 'none'
    }
    if (-not $Record -or ($Record.pid -isnot [int] -and $Record.pid -isnot [long]) -or $Record.pid -le 0 -or $Record.pid -gt 2147483647 -or $Record.mode -cne 'C2_disabled_no_credentials' -or $Record.runpod_effects -isnot [bool] -or $Record.runpod_effects -ne $false -or $Record.journal_opened -isnot [bool] -or $Record.journal_opened -ne $false -or $Record.tunnel_started -isnot [bool] -or $Record.tunnel_started -ne $false) { throw 'Old controller record not accepted.' }
    if ($Process.pid -ne $Record.pid -or $Process.owner_sid -cne $OwnerSid -or $Process.arguments_match -ne $true -or $Process.created_delta_seconds -lt 0 -or $Process.created_delta_seconds -gt 30) { throw 'Old controller exact ownership mismatch.' }
    foreach ($id in @($Listeners) + @($Candidates)) { if ($id -ne $Record.pid) { throw 'Other controller/listener owner; no replacement.' } }
    return 'stop_owned'
}
function Get-C2ControllerCandidates {
    return @(Get-CimInstance Win32_Process -Filter "Name='python.exe' AND CommandLine LIKE '%c2-disabled-controller.py%'" | ForEach-Object { [int]$_.ProcessId })
}
function Get-C2OldSnapshot([string]$OldRoot,[string]$Python,[string]$OwnerSid) {
    $recordPath = Join-Path $OldRoot 'runtime\disabled-controller.json'
    $record = if (Test-Path -LiteralPath $recordPath) { Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json } else { $null }
    $process = $null
    if ($record -and ($record.pid -is [int] -or $record.pid -is [long]) -and $record.pid -gt 0 -and $record.pid -le 2147483647) {
        $row = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $record.pid)
        if ($row) {
            $owner = Invoke-CimMethod -InputObject $row -MethodName GetOwnerSid
            $match = $false
            if ($owner.ReturnValue -eq 0 -and $owner.Sid -ceq $OwnerSid -and [string]::Equals($row.ExecutablePath,$Python,[StringComparison]::OrdinalIgnoreCase)) {
                Initialize-C2NativePath
                $count = 0
                $pointer = [C2NativePath]::CommandLineToArgvW($row.CommandLine,[ref]$count)
                if ($pointer -eq [IntPtr]::Zero) { throw 'Owned process argument parse failed.' }
                try {
                    $args = @()
                    for ($i=0; $i -lt $count; $i++) { $args += [Runtime.InteropServices.Marshal]::PtrToStringUni([Runtime.InteropServices.Marshal]::ReadIntPtr($pointer,$i*[IntPtr]::Size)) }
                    $allowedScripts = @((Join-Path $OldRoot 'runtime\c2-disabled-controller.py'))
                    # Public primitive requires the exact physical OldRoot.
                    # Machine-specific virtualized-path aliases need a separate
                    # reviewed deployment mapping and are not accepted here.
                    $match = $args.Count -eq 3 -and $args[1] -ceq '-B' -and $args[2] -in $allowedScripts
                } finally { [void][C2NativePath]::LocalFree($pointer) }
            }
            $created = [DateTimeOffset]::Parse($record.created_at_utc)
            $delta = ($created.UtcDateTime - $row.CreationDate.ToUniversalTime()).TotalSeconds
            $process = [pscustomobject]@{pid=[int]$row.ProcessId;owner_sid=$owner.Sid;arguments_match=$match;created_delta_seconds=$delta}
        }
    }
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 19180 -ErrorAction SilentlyContinue | ForEach-Object { [int]$_.OwningProcess } | Select-Object -Unique)
    $candidates = @(Get-C2ControllerCandidates)
    $decision = Get-C2StopDecision $record $process $listeners $candidates $OwnerSid
    return [pscustomobject]@{decision=$decision;pid=if($process){$process.pid}else{$null}}
}
