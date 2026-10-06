# Synthetic fixtures only; definitions have no top-level service effects.
$ErrorActionPreference='Stop'
$helpers=Join-Path (Split-Path $PSScriptRoot) 'scripts\c2'
. (Join-Path $helpers 'migration-core.ps1')
. (Join-Path $helpers 'rollback-bound-cas.ps1')
. (Join-Path $helpers 'hook-retain.ps1')
$checks=New-Object Collections.Generic.List[string]
$cases=New-Object Collections.Generic.List[object]
function Require([bool]$Condition,[string]$Message){if(-not $Condition){throw $Message}}
function Deny([scriptblock]$Action){$denied=$false;try{& $Action|Out-Null}catch{$denied=$true};Require $denied 'Expected refusal'}
function Sha([string]$Path){(Get-FileHash -LiteralPath $Path).Hash.ToLowerInvariant()}
function Load-Definitions([string]$Path){
 $tokens=$null;$errors=$null;$tree=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$tokens,[ref]$errors)
 Require (@($errors).Count -eq 0) 'Script parse failure'
 return @($tree.EndBlock.Statements|Where-Object{$_ -is [Management.Automation.Language.FunctionDefinitionAst]})
}
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('qmc-c2-synthetic-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture|Out-Null
$patched=Join-Path $fixture 'public-patched.py';$original=Join-Path $fixture 'public-original.py';$hookFixture=Join-Path $fixture 'public-hook.py'
[IO.File]::WriteAllText($patched,'public synthetic patched source')
[IO.File]::WriteAllText($original,'public synthetic original source')
[IO.File]::WriteAllText($hookFixture,'public synthetic hook source')
$packet=[pscustomobject]@{main_sha256=(Sha $patched);reapply_main_cas_from_sha256=(Sha $original);hook_sha256=(Sha $hookFixture)}
Initialize-C2RollbackBoundNative
function Pass([string]$Name){$checks.Add($Name)}
$script:originalHash=${function:Get-C2RollbackBoundHash}
$script:originalRename=${function:Rename-C2RollbackBoundFile}
$script:originalDisplaced=${function:Get-C2RollbackDisplacedPath}
function New-Case([string]$Name){
 Set-Item function:script:Get-C2RollbackBoundHash $script:originalHash
 Set-Item function:script:Rename-C2RollbackBoundFile $script:originalRename
 Set-Item function:script:Get-C2RollbackDisplacedPath $script:originalDisplaced
 $dir=Join-Path $fixture $Name;New-Item -ItemType Directory -Path $dir|Out-Null
 $script:target=Join-Path $dir 'main.py';$script:backup=Join-Path $dir 'original.py';$script:incoming=Join-Path $dir 'concurrent.py'
 Copy-C2Exclusive $patched $script:target $packet.main_sha256
 Copy-C2Exclusive $original $script:backup $packet.reapply_main_cas_from_sha256
 # C2 deployment requires protected ACLs; inherited temporary-directory ACLs
 # are not its deployment precondition. Protect only these synthetic files.
 foreach($file in @($script:target,$script:backup)){
  $security=Get-Acl -LiteralPath $file
  $security.SetAccessRuleProtection($true,$true)
  Set-Acl -LiteralPath $file -AclObject $security
 }
 [IO.File]::WriteAllText($script:incoming,('public synthetic concurrent update '+$Name))
 $script:unknownSha=Sha $script:incoming;$script:sddl=(Get-Acl -LiteralPath $script:target).Sddl
 $script:phase=0;$script:writerDenied=$false;$script:collision=$null
}
function Invoke-Fixture{Invoke-C2RollbackBoundCas $script:target $script:backup $packet.main_sha256 $packet.reapply_main_cas_from_sha256 $script:sddl}
function Assert-Restored{
 Assert-C2Hash $script:target $packet.reapply_main_cas_from_sha256
 $files=@(Get-ChildItem -LiteralPath (Split-Path $script:target) -Filter '*.qmc-rollback-displaced-*.py')
 Require ($files.Count -eq 1) 'Displaced source count mismatch';Assert-C2Hash $files[0].FullName $packet.main_sha256
 Require ((Get-Acl -LiteralPath $script:target).Sddl -ceq $script:sddl) 'Restored ACL changed'
 Require (-not (Test-Path -LiteralPath ($script:target+'.qmc-rollback.pending'))) 'Pending unexpectedly remains after success'
}
function Record([string]$Name){
 $dir=Split-Path $script:target
 $files=@(Get-ChildItem -LiteralPath $dir -File|ForEach-Object{[ordered]@{name=$_.Name;sha256=Sha $_.FullName;bytes=$_.Length}})
 $cases.Add([ordered]@{case=$Name;files=$files;canonical_present=(Test-Path -LiteralPath $script:target);writer_denied=$script:writerDenied})
 Pass $Name
}
New-Case 'normal_restore';$result=Invoke-Fixture;Require ($result.status -ceq 'restored') 'Restore status';Assert-Restored;Record 'Known corrected source restored with exact SHA ACL and retained displaced source'
New-Case 'unknown_before_lock'
Assert-C2Hash $script:target $packet.main_sha256
[IO.File]::WriteAllBytes($script:target,[IO.File]::ReadAllBytes($script:incoming))
Deny {Invoke-Fixture};Assert-C2Hash $script:target $script:unknownSha
Require (@(Get-ChildItem -LiteralPath (Split-Path $script:target) -Filter '*.qmc-rollback*').Count -eq 0) 'Unknown created rollback artifacts'
$write=[IO.File]::Open($script:target,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::None);$write.Dispose()
Record 'Concurrent content update after preflight rejected preserved and lock released'
New-Case 'replacement_before_lock'
Assert-C2Hash $script:target $packet.main_sha256
[IO.File]::Replace($script:incoming,$script:target,[NullString]::Value,$true)
Deny {Invoke-Fixture};Assert-C2Hash $script:target $script:unknownSha;Record 'Concurrent namespace replacement after preflight rejected and preserved'
New-Case 'write_while_locked'
function Get-C2RollbackBoundHash([IO.FileStream]$Stream){
 $value=[C2RollbackBoundNativeV4]::Hash($Stream)
 if($script:phase++ -eq 0){try{[IO.File]::WriteAllText($script:target,'unexpected write')}catch{$script:writerDenied=$true};Require $script:writerDenied 'Write while locked succeeded'}
 return $value
}
Invoke-Fixture|Out-Null;Require $script:writerDenied 'Writer not denied';Assert-Restored;Record 'Write between bound hash and rename blocked by exclusive handle'
New-Case 'replace_while_locked'
function Get-C2RollbackBoundHash([IO.FileStream]$Stream){
 $value=[C2RollbackBoundNativeV4]::Hash($Stream)
 if($script:phase++ -eq 0){try{[IO.File]::Replace($script:incoming,$script:target,[NullString]::Value,$true)}catch{$script:writerDenied=$true};Require $script:writerDenied 'Replace while locked succeeded'}
 return $value
}
Invoke-Fixture|Out-Null;Assert-C2Hash $script:incoming $script:unknownSha;Assert-Restored;Record 'Namespace replacement between bound hash and rename blocked and incoming retained'
New-Case 'move_while_locked'
function Get-C2RollbackBoundHash([IO.FileStream]$Stream){
 $value=[C2RollbackBoundNativeV4]::Hash($Stream)
 if($script:phase++ -eq 0){try{[IO.File]::Move($script:target,($script:target+'.racer'))}catch{$script:writerDenied=$true};Require $script:writerDenied 'Move while locked succeeded'}
 return $value
}
Invoke-Fixture|Out-Null;Assert-Restored;Record 'Namespace move between bound hash and rename blocked'
New-Case 'canonical_collision_between_renames'
function Rename-C2RollbackBoundFile([IO.FileStream]$Stream,[string]$Destination){
 [C2RollbackBoundNativeV4]::RenameNoReplace($Stream,$Destination)
 if($script:phase++ -eq 0){$script:collision=$Destination;[IO.File]::Move($script:incoming,$script:target)}
}
Deny {Invoke-Fixture};Assert-C2Hash $script:target $script:unknownSha;Assert-C2Hash $script:collision $packet.main_sha256
Assert-C2Hash ($script:target+'.qmc-rollback.pending') $packet.reapply_main_cas_from_sha256
Record 'Unknown canonical entry created between renames untouched both verified sources retained'
New-Case 'fault_between_renames'
function Rename-C2RollbackBoundFile([IO.FileStream]$Stream,[string]$Destination){
 [C2RollbackBoundNativeV4]::RenameNoReplace($Stream,$Destination)
 if($script:phase++ -eq 0){$script:collision=$Destination;throw 'Synthetic fault after source displacement'}
}
Deny {Invoke-Fixture};Require (-not (Test-Path -LiteralPath $script:target)) 'Unexpected canonical after fault'
Assert-C2Hash $script:collision $packet.main_sha256;Assert-C2Hash ($script:target+'.qmc-rollback.pending') $packet.reapply_main_cas_from_sha256
Record 'Fault after first rename leaves canonical absent and both complete sources retained for manual recovery'
New-Case 'already_original';[IO.File]::WriteAllBytes($script:target,[IO.File]::ReadAllBytes($script:backup))
$result=Invoke-Fixture;Require ($result.status -ceq 'already_original') 'Expected no-op'
Require (@(Get-ChildItem -LiteralPath (Split-Path $script:target) -Filter '*.qmc-rollback*').Count -eq 0) 'No-op created artifact';Record 'Exact original source is no-op'
New-Case 'tampered_backup';[IO.File]::WriteAllBytes($script:backup,[IO.File]::ReadAllBytes($script:incoming))
Deny {Invoke-Fixture};Assert-C2Hash $script:target $packet.main_sha256;Assert-C2Hash $script:backup $script:unknownSha;Record 'Unknown backup rejected current source and unknown backup retained'
New-Case 'pending_collision';[IO.File]::Move($script:incoming,($script:target+'.qmc-rollback.pending'))
Deny {Invoke-Fixture};Assert-C2Hash $script:target $packet.main_sha256;Assert-C2Hash ($script:target+'.qmc-rollback.pending') $script:unknownSha;Record 'Pending name collision rejected without overwriting either file'
New-Case 'displaced_collision'
function Get-C2RollbackDisplacedPath([string]$Main){return $script:incoming}
Deny {Invoke-Fixture};Assert-C2Hash $script:target $packet.main_sha256;Assert-C2Hash $script:incoming $script:unknownSha
Assert-C2Hash ($script:target+'.qmc-rollback.pending') $packet.reapply_main_cas_from_sha256;Record 'Displaced name collision rejected current source and unknown destination retained'
New-Case 'existing_writer'
$writer=[IO.File]::Open($script:target,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
try{Deny {Invoke-Fixture}}finally{$writer.Dispose()}
Assert-C2Hash $script:target $packet.main_sha256;Record 'Preexisting writer makes exclusive open fail before mutation'
New-Case 'manual_main_recovery_after_gap'
function Rename-C2RollbackBoundFile([IO.FileStream]$Stream,[string]$Destination){[C2RollbackBoundNativeV4]::RenameNoReplace($Stream,$Destination);throw 'Synthetic gap fault'}
Deny {Invoke-Fixture}
$held=[C2RollbackBoundNativeV4]::OpenExclusive($script:target+'.qmc-rollback.pending')
try{Require (([C2RollbackBoundNativeV4]::Hash($held)) -ceq $packet.reapply_main_cas_from_sha256) 'Recovery original pin';[C2RollbackBoundNativeV4]::RenameNoReplace($held,$script:target)}finally{$held.Dispose()}
Assert-Restored;Record 'Manual gap recovery restores exact prepared original only into absent canonical entry'
New-Case 'manual_main_recovery_collision'
function Rename-C2RollbackBoundFile([IO.FileStream]$Stream,[string]$Destination){[C2RollbackBoundNativeV4]::RenameNoReplace($Stream,$Destination);throw 'Synthetic gap fault'}
Deny {Invoke-Fixture};[IO.File]::Move($script:incoming,$script:target)
$held=[C2RollbackBoundNativeV4]::OpenExclusive($script:target+'.qmc-rollback.pending')
try{Require (([C2RollbackBoundNativeV4]::Hash($held)) -ceq $packet.reapply_main_cas_from_sha256) 'Recovery original pin';Deny {[C2RollbackBoundNativeV4]::RenameNoReplace($held,$script:target)}}finally{$held.Dispose()}
Assert-C2Hash $script:target $script:unknownSha;Assert-C2Hash ($script:target+'.qmc-rollback.pending') $packet.reapply_main_cas_from_sha256
Record 'Manual gap recovery collision preserves unknown canonical entry and prepared original'
Write-Output ('C2 main CAS cases PASS: '+$checks.Count)
