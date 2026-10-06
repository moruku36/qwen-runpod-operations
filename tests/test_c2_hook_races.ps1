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
$originalHash=${function:Get-C2RollbackBoundHash};$originalRename=${function:Rename-C2RollbackBoundFile};$originalPath=${function:Get-C2RollbackHookRetainedPath}
function New-Case([string]$Name){
 Set-Item function:script:Get-C2RollbackBoundHash $script:originalHash
 Set-Item function:script:Rename-C2RollbackBoundFile $script:originalRename
 Set-Item function:script:Get-C2RollbackHookRetainedPath $script:originalPath
 $dir=Join-Path $fixture $Name;New-Item -ItemType Directory -Path $dir|Out-Null
 $script:hook=Join-Path $dir 'qmc_ondemand_hook.py';$script:incoming=Join-Path $dir 'unknown.py'
 Copy-C2Exclusive $hookFixture $script:hook $packet.hook_sha256
 [IO.File]::WriteAllText($script:incoming,('public synthetic unknown hook '+$Name))
 $script:unknownSha=Sha $script:incoming;$script:sddl=(Get-Acl -LiteralPath $script:hook).Sddl
 $script:phase=0;$script:writerDenied=$false;$script:retained=$null
}
function Invoke-Hook{Invoke-C2RollbackHookRetain $script:hook $packet.hook_sha256}
function Assert-Retained{
 $files=@(Get-ChildItem -LiteralPath (Split-Path $script:hook) -Filter '*.qmc-rollback-retained-*.py')
 Require ($files.Count -eq 1) 'Retained hook count mismatch';Assert-C2Hash $files[0].FullName $packet.hook_sha256
 Require ((Get-Acl -LiteralPath $files[0].FullName).Sddl -ceq $script:sddl) 'Hook ACL changed'
 $script:retained=$files[0].FullName
}
function Record([string]$Name){
 $files=@(Get-ChildItem -LiteralPath (Split-Path $script:hook) -File|ForEach-Object{[ordered]@{name=$_.Name;sha256=Sha $_.FullName;bytes=$_.Length}})
 $cases.Add([ordered]@{case=$Name;files=$files;canonical_present=(Test-Path -LiteralPath $script:hook);writer_denied=$script:writerDenied});$checks.Add($Name)
}
New-Case 'normal_retain';$r=Invoke-Hook;Require ($r.status -ceq 'retained') 'Retain status';Require (-not (Test-Path -LiteralPath $script:hook)) 'Canonical hook remains';Assert-Retained;Record 'Verified hook retained with exact SHA and unchanged ACL; no deletion'
New-Case 'unknown_after_earlier_hash';Assert-C2Hash $script:hook $packet.hook_sha256
[IO.File]::WriteAllBytes($script:hook,[IO.File]::ReadAllBytes($script:incoming));Deny {Invoke-Hook};Assert-C2Hash $script:hook $script:unknownSha
Require (@(Get-ChildItem -LiteralPath (Split-Path $script:hook) -Filter '*.qmc-rollback-retained-*').Count -eq 0) 'Unknown displaced'
$w=[IO.File]::Open($script:hook,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::None);$w.Dispose()
Record 'Review reproduced unknown update after earlier path hash rejected preserved and handle released'
New-Case 'namespace_replace_before_lock';Assert-C2Hash $script:hook $packet.hook_sha256
[IO.File]::Replace($script:incoming,$script:hook,[NullString]::Value,$true);Deny {Invoke-Hook};Assert-C2Hash $script:hook $script:unknownSha;Record 'Namespace replacement before bound hook open rejected and preserved'
New-Case 'write_after_bound_hash'
function Get-C2RollbackBoundHash([IO.FileStream]$Stream){$v=[C2RollbackBoundNativeV4]::Hash($Stream);if($script:phase++ -eq 0){try{[IO.File]::WriteAllText($script:hook,'unknown overwrite')}catch{$script:writerDenied=$true};Require $script:writerDenied 'Writer succeeded under lock'};return $v}
Invoke-Hook|Out-Null;Assert-Retained;Record 'Concurrent write after bound hash denied by exclusive handle'
New-Case 'replace_after_bound_hash'
function Get-C2RollbackBoundHash([IO.FileStream]$Stream){$v=[C2RollbackBoundNativeV4]::Hash($Stream);if($script:phase++ -eq 0){try{[IO.File]::Replace($script:incoming,$script:hook,[NullString]::Value,$true)}catch{$script:writerDenied=$true};Require $script:writerDenied 'Replacement succeeded under lock'};return $v}
Invoke-Hook|Out-Null;Assert-Retained;Assert-C2Hash $script:incoming $script:unknownSha;Record 'Concurrent namespace replacement after bound hash denied and incoming preserved'
New-Case 'delete_after_bound_hash'
function Get-C2RollbackBoundHash([IO.FileStream]$Stream){$v=[C2RollbackBoundNativeV4]::Hash($Stream);if($script:phase++ -eq 0){try{[IO.File]::Delete($script:hook)}catch{$script:writerDenied=$true};Require $script:writerDenied 'Delete succeeded under lock'};return $v}
Invoke-Hook|Out-Null;Assert-Retained;Record 'Concurrent unlink after bound hash denied; verified hook remains recoverable'
New-Case 'retained_destination_collision'
function Get-C2RollbackHookRetainedPath([string]$Hook){return $script:incoming}
Deny {Invoke-Hook};Assert-C2Hash $script:hook $packet.hook_sha256;Assert-C2Hash $script:incoming $script:unknownSha;Record 'Retained-name collision preserves verified canonical hook and unknown destination'
New-Case 'canonical_created_after_rename'
function Rename-C2RollbackBoundFile([IO.FileStream]$Stream,[string]$Destination){[C2RollbackBoundNativeV4]::RenameNoReplace($Stream,$Destination);$script:retained=$Destination;[IO.File]::Move($script:incoming,$script:hook)}
Invoke-Hook|Out-Null;Assert-C2Hash $script:hook $script:unknownSha;Assert-Retained;Record 'Unknown canonical hook created after retention is untouched and verified hook retained'
New-Case 'fault_after_rename'
function Rename-C2RollbackBoundFile([IO.FileStream]$Stream,[string]$Destination){[C2RollbackBoundNativeV4]::RenameNoReplace($Stream,$Destination);throw 'Synthetic fault immediately after retention'}
Deny {Invoke-Hook};Assert-Retained;Require (-not (Test-Path -LiteralPath $script:hook)) 'Canonical unexpectedly exists';Record 'Fault after rename retains complete verified hook for manual recovery'
New-Case 'already_absent';[IO.File]::Move($script:hook,($script:hook+'.fixture-away'))
$r=Invoke-Hook;Require ($r.status -ceq 'already_absent') 'Absent hook no-op';Record 'Absent hook is no-op with no delete or recreate'
New-Case 'preexisting_writer';$writer=[IO.File]::Open($script:hook,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
try{Deny {Invoke-Hook}}finally{$writer.Dispose()};Assert-C2Hash $script:hook $packet.hook_sha256;Record 'Preexisting hook writer causes refusal before mutation'
New-Case 'restore_retained_hook';$r=Invoke-Hook
$held=[C2RollbackBoundNativeV4]::OpenExclusive($r.retained_hook)
try{Require (([C2RollbackBoundNativeV4]::Hash($held)) -ceq $packet.hook_sha256) 'Recovery source hash';[C2RollbackBoundNativeV4]::RenameNoReplace($held,$script:hook)}finally{$held.Dispose()}
Assert-C2Hash $script:hook $packet.hook_sha256;Record 'Manual recovery simulation restores verified retained hook only into absent canonical entry'
New-Case 'recovery_collision';$r=Invoke-Hook;[IO.File]::Move($script:incoming,$script:hook)
$held=[C2RollbackBoundNativeV4]::OpenExclusive($r.retained_hook)
try{Deny {[C2RollbackBoundNativeV4]::RenameNoReplace($held,$script:hook)}}finally{$held.Dispose()}
Assert-C2Hash $script:hook $script:unknownSha;Assert-C2Hash $r.retained_hook $packet.hook_sha256;Record 'Manual recovery collision refuses to overwrite unknown canonical hook'

# Extract only exclusive-create definitions, never invoke credential prompts or operators.
foreach($path in @('operators\c2-stop-disabled-controller.ps1','operators\c2-start-disabled-controller.ps1')){
 foreach($definition in (Load-Definitions (Join-Path $helpers ('templates\'+$path.Replace('operators\','')+'.in')))){. ([scriptblock]::Create($definition.Extent.Text))}
 New-Case ('flag_'+[IO.Path]::GetFileNameWithoutExtension($path));$flag=Join-Path (Split-Path $script:hook) 'stop.flag'
 Require (-not (Test-Path -LiteralPath $flag)) 'Fixture preflight';[IO.File]::WriteAllBytes($flag,[IO.File]::ReadAllBytes($script:incoming))
 Deny {New-C2StopFlagExclusive $flag};Assert-C2Hash $flag $script:unknownSha
 $new=Join-Path (Split-Path $script:hook) 'new-stop.flag';New-C2StopFlagExclusive $new;Require ([IO.File]::ReadAllText($new) -ceq 'stop') 'New stop marker';Record ('Stop-flag create-new preserves concurrent collision: '+$path)
}
$startPath=Join-Path $helpers 'templates\c2-start-disabled-controller.ps1.in'
$tokens=$null;$errors=$null;$tree=[Management.Automation.Language.Parser]::ParseFile($startPath,[ref]$tokens,[ref]$errors)
$gate=@($tree.FindAll({param($n)$n -is [Management.Automation.Language.IfStatementAst] -and $n.Extent.Text -match 'Retained stop flag collision'},$true));Require ($gate.Count -eq 1) 'Stale flag gate count'
New-Case 'stale_flag_preservation';$stopPath=$script:incoming;Deny {& ([scriptblock]::Create($gate[0].Extent.Text))};Assert-C2Hash $stopPath $script:unknownSha;Record 'Start stale-flag gate rejects and preserves unknown file; no cleanup'
$inputPath=Join-Path $helpers 'templates\c2-prepare-backend-access.ps1.in'
$function=@(Load-Definitions $inputPath|Where-Object{$_.Name -ceq 'Write-C2NewJsonExclusive'});Require ($function.Count -eq 1) 'Exclusive input writer count'
. ([scriptblock]::Create($function[0].Extent.Text))
New-Case 'input_save_collision';$jsonPath=$script:incoming;Deny {Write-C2NewJsonExclusive $jsonPath '{"public_test":true}'};Assert-C2Hash $jsonPath $script:unknownSha
$first=Join-Path (Split-Path $script:hook) 'public-first.json';Write-C2NewJsonExclusive $first '{"synthetic":true}'
$firstSha=Sha $first;Deny {Write-C2NewJsonExclusive $jsonPath '{"synthetic_second":true}'};Assert-C2Hash $first $firstSha;Assert-C2Hash $jsonPath $script:unknownSha
Record 'Two-file save collision preserves first newly-created JSON and unknown second file; no rollback deletion'
Write-Output ('C2 hook/auxiliary cases PASS: '+$checks.Count)
