# Embedded into the rollback operator; definitions only, no top-level effects.
function Initialize-C2RollbackBoundNative {
    if ('C2RollbackBoundNativeV4' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.ComponentModel;
using System.Security.Cryptography;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class C2RollbackBoundNativeV4 {
 [StructLayout(LayoutKind.Sequential)] struct FileInfo {
  public uint Attributes; public System.Runtime.InteropServices.ComTypes.FILETIME Created;
  public System.Runtime.InteropServices.ComTypes.FILETIME Accessed; public System.Runtime.InteropServices.ComTypes.FILETIME Written;
  public uint Volume,SizeHigh,SizeLow,Links,IndexHigh,IndexLow;
 }
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern SafeFileHandle CreateFile(string p,uint access,uint share,IntPtr sa,uint mode,uint flags,IntPtr template);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetFileInformationByHandle(SafeFileHandle h,out FileInfo info);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern uint GetFinalPathNameByHandle(SafeFileHandle h,StringBuilder b,uint size,uint flags);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetFileInformationByHandle(SafeFileHandle h,int type,IntPtr buffer,uint size);
 public static FileStream OpenExclusive(string path) {
  // READ + DELETE; deny all other read/write/delete opens while this handle lives.
  SafeFileHandle h=CreateFile(path,0x80010000u,0,IntPtr.Zero,3,0x00200080u,IntPtr.Zero);
  if(h.IsInvalid) { int error=Marshal.GetLastWin32Error();h.Dispose();throw new Win32Exception(error,"Rollback exclusive open refused"); }
  try {
   FileInfo info;
   if(!GetFileInformationByHandle(h,out info)) throw new Win32Exception(Marshal.GetLastWin32Error());
   if((info.Attributes & 0x400u)!=0 || info.Links!=1) throw new IOException("Rollback reparse/hardlink refused");
   StringBuilder buffer=new StringBuilder(4096);
   uint length=GetFinalPathNameByHandle(h,buffer,4096,0);
   if(length==0 || length>=4096) throw new IOException("Rollback handle path unavailable");
   string actual=buffer.ToString();if(actual.StartsWith("\\\\?\\"))actual=actual.Substring(4);
   if(!String.Equals(actual,Path.GetFullPath(path),StringComparison.OrdinalIgnoreCase)) throw new IOException("Rollback handle path mismatch");
   return new FileStream(h,FileAccess.Read);
  } catch {h.Dispose();throw;}
 }
 public static string Hash(FileStream stream) {
  stream.Position=0;byte[] value;
  using(SHA256 sha=SHA256.Create()) {value=sha.ComputeHash(stream);}
  stream.Position=0;return BitConverter.ToString(value).Replace("-","").ToLowerInvariant();
 }
 public static void RenameNoReplace(FileStream stream,string destination) {
  string path=Path.GetFullPath(destination);byte[] name=Encoding.Unicode.GetBytes(path);
  int rootOffset=IntPtr.Size==8?8:4;int lengthOffset=rootOffset+IntPtr.Size;int nameOffset=lengthOffset+4;
  // ReplaceIfExists=false, RootDirectory=NULL; never overwrite another entry.
  byte[] bytes=new byte[nameOffset+name.Length+2];Buffer.BlockCopy(name,0,bytes,nameOffset,name.Length);
  IntPtr buffer=Marshal.AllocHGlobal(bytes.Length);
  try {
   Marshal.Copy(bytes,0,buffer,bytes.Length);Marshal.WriteInt32(buffer,lengthOffset,name.Length);
   if(!SetFileInformationByHandle(stream.SafeFileHandle,3,buffer,(uint)bytes.Length)) throw new Win32Exception(Marshal.GetLastWin32Error(),"Rollback no-replace rename refused");
  } finally {Marshal.FreeHGlobal(buffer);}
 }
}
'@
}
function Get-C2RollbackBoundHash([IO.FileStream]$Stream) {
    return [C2RollbackBoundNativeV4]::Hash($Stream)
}
function Rename-C2RollbackBoundFile([IO.FileStream]$Stream,[string]$Destination) {
    [C2RollbackBoundNativeV4]::RenameNoReplace($Stream,$Destination)
}
function Get-C2RollbackDisplacedPath([string]$Main) {
    return $Main + '.qmc-rollback-displaced-' + [Guid]::NewGuid().ToString('N') + '.py'
}
function Invoke-C2RollbackBoundCas([string]$Main,[string]$Backup,[string]$PatchedSha,[string]$OriginalSha,[string]$OriginalSddl) {
    if($PatchedSha -cnotmatch '^[0-9a-f]{64}$' -or $OriginalSha -cnotmatch '^[0-9a-f]{64}$') {throw 'Rollback hash shape invalid.'}
    Assert-C2PhysicalPath $Main
    Assert-C2PhysicalPath $Backup
    Initialize-C2RollbackBoundNative
    $source=$null;$original=$null;$pending=$null
    $temporary=$Main+'.qmc-rollback.pending'
    $displaced=$null
    try {
        # The same exclusive handle is hashed and renamed. Never trust an earlier
        # path hash, and never close this handle between verification and mutation.
        $source=[C2RollbackBoundNativeV4]::OpenExclusive($Main)
        $current=Get-C2RollbackBoundHash $source
        if($current -ceq $OriginalSha) {return [pscustomobject]@{status='already_original';displaced_source=$null}}
        if($current -cne $PatchedSha) {throw 'Rollback unknown source under lock; unchanged and retained.'}
        $original=[C2RollbackBoundNativeV4]::OpenExclusive($Backup)
        if((Get-C2RollbackBoundHash $original) -cne $OriginalSha) {throw 'Rollback backup changed under lock; source retained.'}
        $output=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try {$original.Position=0;$original.CopyTo($output);$output.Flush($true)} finally {$output.Dispose()}
        $security=New-Object Security.AccessControl.FileSecurity
        $security.SetSecurityDescriptorSddlForm($OriginalSddl)
        Set-Acl -LiteralPath $temporary -AclObject $security
        $pending=[C2RollbackBoundNativeV4]::OpenExclusive($temporary)
        if((Get-C2RollbackBoundHash $pending) -cne $OriginalSha) {throw 'Rollback pending original changed; source retained.'}
        $displaced=Get-C2RollbackDisplacedPath $Main
        # Retain the verified source without overwriting a backup name. Then
        # install only into an absent canonical entry, never replace a racer.
        Rename-C2RollbackBoundFile $source $displaced
        try {Rename-C2RollbackBoundFile $pending $Main}
        catch {throw ('Rollback destination collision/failure; unknown canonical entry untouched; verified source retained at '+$displaced+' and original retained at '+$temporary+'.')}
        if((Get-C2RollbackBoundHash $pending) -cne $OriginalSha) {throw 'Rollback installed handle verification failed; retained backups need review.'}
        return [pscustomobject]@{status='restored';displaced_source=$displaced}
    } finally {
        if($pending){$pending.Dispose()}
        if($original){$original.Dispose()}
        if($source){$source.Dispose()}
    }
}
