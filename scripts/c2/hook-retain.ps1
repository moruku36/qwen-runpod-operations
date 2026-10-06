# Embedded definitions only. A verified hook is retained, never unlinked.
function Get-C2RollbackHookRetainedPath([string]$Hook) {
    return $Hook+'.qmc-rollback-retained-'+[Guid]::NewGuid().ToString('N')+'.py'
}
function Invoke-C2RollbackHookRetain([string]$Hook,[string]$ExpectedSha) {
    if($ExpectedSha -cnotmatch '^[0-9a-f]{64}$') {throw 'Hook hash shape invalid.'}
    Assert-C2PhysicalPath ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Hook)))
    Initialize-C2RollbackBoundNative
    $held=$null
    try {
        try {$held=[C2RollbackBoundNativeV4]::OpenExclusive($Hook)}
        catch {
            $cause=$_.Exception.GetBaseException()
            if($cause -is [ComponentModel.Win32Exception] -and $cause.NativeErrorCode -eq 2) {
                return [pscustomobject]@{status='already_absent';retained_hook=$null}
            }
            throw
        }
        if((Get-C2RollbackBoundHash $held) -cne $ExpectedSha) {throw 'Unknown hook under lock; unchanged and retained at canonical path.'}
        $retained=Get-C2RollbackHookRetainedPath $Hook
        # Same exclusive file object from hash through no-replace rename.
        # An incoming destination entry is never overwritten; no path unlink.
        Rename-C2RollbackBoundFile $held $retained
        if((Get-C2RollbackBoundHash $held) -cne $ExpectedSha) {throw ('Retained hook verification failed; review '+$retained)}
        return [pscustomobject]@{status='retained';retained_hook=$retained}
    } finally {if($held){$held.Dispose()}}
}
