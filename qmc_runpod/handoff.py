"""Explicit process-boundary artifact handoff; no discovery, fallback downloads or network."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from . import layout, pins

class HandoffError(RuntimeError):
    pass

def sha256(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(16*1024*1024),b''):digest.update(block)
    return digest.hexdigest()

def _file(value, within: Path, *, executable: bool=False) -> Path:
    if not isinstance(value,str) or not Path(value).is_absolute():
        raise HandoffError('artifact_path_not_absolute')
    path=Path(value).resolve()
    if not path.is_relative_to(within.resolve()) or not path.is_file() or path.stat().st_size<=0:
        raise HandoffError('artifact_missing_empty_or_outside_workspace')
    if not os.access(path,os.R_OK) or (executable and not os.access(path,os.X_OK)):
        raise HandoffError('artifact_not_readable_or_executable')
    return path

def read_build(root: Path) -> dict:
    """Revalidate the two pinned artifacts and exact executable before UI startup.

    Model SHA verification belongs to the immediately preceding build stage;
    this immutable run handoff checks its record identities and current sizes.
    It does not authorize moving artifacts into a new run or resuming a trial.
    """
    paths=layout.paths(root)
    try:info=json.loads((paths['runs']/'build.json').read_text())
    except (OSError,ValueError) as exc:raise HandoffError('build_metadata_missing_or_invalid') from exc
    if info.get('handoff_schema')!=1 or info.get('llama_commit')!=pins.LLAMA_CPP_COMMIT:
        raise HandoffError('build_metadata_identity_mismatch')
    server=_file(info.get('server'),paths['llama_bin'],executable=True)
    if server.name!='llama-server' or not server.parent.name.startswith(pins.LLAMA_CPP_COMMIT[:10]+'-sm'):
        raise HandoffError('server_identity_mismatch')
    if info.get('server_sha256')!=sha256(server):raise HandoffError('server_hash_mismatch')
    values=info.get('model_paths');records=info.get('records')
    if not isinstance(values,list) or len(values)!=2 or not isinstance(records,list) or len(records)!=2:
        raise HandoffError('exact_chat_and_mmproj_handoff_required')
    models=[]
    for index,role in enumerate(('chat','mmproj')):
        spec=pins.MODELS[role];record=records[index]
        if not isinstance(record,dict) or any(record.get(k)!=v for k,v in
                {'role':role,'file':spec['file'],'revision':spec['revision'],'sha256_verified':True}.items()):
            raise HandoffError('model_verification_record_mismatch')
        model=_file(values[index],paths['hf_cache'])
        if model.stat().st_size!=record.get('size_bytes'):raise HandoffError('model_size_changed')
        models.append(str(model))
    return {**info,'server':str(server),'model_paths':models}


def failure_code(exc: BaseException) -> str:
    """Allowlisted diagnosis only; never copy command lines, credentials or raw errors."""
    allowed = {
        "artifact_path_not_absolute", "artifact_missing_empty_or_outside_workspace",
        "artifact_not_readable_or_executable", "build_metadata_missing_or_invalid",
        "build_metadata_identity_mismatch", "server_identity_mismatch", "server_hash_mismatch",
        "exact_chat_and_mmproj_handoff_required", "model_verification_record_mismatch",
        "model_size_changed", "explicit_llama_server_path_required",
        "explicit_llama_server_missing_or_not_executable", "backend_resolved_a_different_server",
        "explicit_verified_chat_and_mmproj_required", "backend_cannot_bind_verified_model_paths",
        "backend_port_in_use_no_process_was_killed", "llama_server_runtime_probe_failed",
        "effective_context_changed_from_approved_8192",
    }
    current = exc
    for _ in range(4):
        message = str(current)
        if message in allowed:
            return message
        for line in message.splitlines()[:20]:
            candidate = line.strip().removeprefix("- ")
            if candidate in allowed:
                return candidate
        if "llama-server が見つかりません" in message:
            return "llama_server_not_found"
        if any(marker in message.lower() for marker in ("out of memory", "cudamalloc failed", "vram不足")):
            return "model_load_memory_failure"
        current = current.__cause__
        if current is None:
            break
    return "unclassified_runtime_failure"
