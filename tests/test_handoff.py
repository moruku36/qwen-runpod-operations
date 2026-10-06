"""CPU-only artifact identity, failure observability and actual executable probes."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from qmc_runpod import handoff,layout,llama,pins,smoke


def artifacts(root):
    p=layout.paths(root)
    server=p['llama_bin']/(pins.LLAMA_CPP_COMMIT[:10]+'-sm80-py311-x86_64')/'llama-server'
    server.parent.mkdir(parents=True);server.write_text('#!/bin/sh\necho version-test\n');server.chmod(0o755)
    p['hf_cache'].mkdir();p['runs'].mkdir()
    models=[];records=[]
    for role in ('chat','mmproj'):
        file=p['hf_cache']/(role+'.gguf');file.write_bytes((role+'-cpu-fixture').encode());models.append(str(file))
        spec=pins.MODELS[role]
        records.append({'role':role,'file':spec['file'],'revision':spec['revision'],'sha256_verified':True,'size_bytes':file.stat().st_size})
    data={'handoff_schema':1,'llama_commit':pins.LLAMA_CPP_COMMIT,'server':str(server),'server_sha256':handoff.sha256(server),'model_paths':models,'records':records,'build':{}}
    path=p['runs']/'build.json';path.write_text(json.dumps(data));return data,path


def test_explicit_artifact_handoff_preserves_binary_and_both_models(tmp_path):
    data,path=artifacts(tmp_path)
    result=handoff.read_build(tmp_path)
    assert result['server']==data['server'] and result['model_paths']==data['model_paths']


@pytest.mark.parametrize('mutation,reason',[
    (lambda d:d.update(handoff_schema=0),'identity'),
    (lambda d:d.update(llama_commit='0'*40),'identity'),
    (lambda d:d.update(server='relative/llama-server'),'absolute'),
    (lambda d:d.update(server_sha256='0'*64),'hash'),
    (lambda d:d.update(model_paths=d['model_paths'][:1]),'exact_chat'),
    (lambda d:d.update(model_paths=[d['model_paths'][0],None]),'absolute'),
    (lambda d:d['records'][0].update(sha256_verified=False),'record'),
    (lambda d:d['records'][1].update(revision='bad'),'record'),
    (lambda d:d['records'][0].update(size_bytes=99),'size'),
])
def test_bad_handoff_fails_closed(tmp_path,mutation,reason):
    data,path=artifacts(tmp_path);mutation(data);path.write_text(json.dumps(data))
    with pytest.raises(handoff.HandoffError,match=reason):handoff.read_build(tmp_path)


def test_changed_binary_is_rejected_before_launch(tmp_path):
    data,_=artifacts(tmp_path);Path(data['server']).write_text('#!/bin/sh\necho replaced\n')
    with pytest.raises(handoff.HandoffError,match='hash'):handoff.read_build(tmp_path)


def test_model_outside_current_root_is_rejected(tmp_path):
    root=tmp_path/'root';data,path=artifacts(root)
    external=tmp_path/'outside.gguf';external.write_bytes(b'outside')
    data['model_paths'][0]=str(external);path.write_text(json.dumps(data))
    with pytest.raises(handoff.HandoffError,match='outside'):handoff.read_build(root)


def test_model_symlink_inside_cache_resolves_without_unpinned_download(tmp_path):
    data,path=artifacts(tmp_path);target=Path(data['model_paths'][0]);link=target.parent/'snapshot.gguf';link.symlink_to(target)
    data['model_paths'][0]=str(link);path.write_text(json.dumps(data))
    assert handoff.read_build(tmp_path)['model_paths'][0]==str(target)


def test_non_executable_server_is_rejected(tmp_path):
    data,_=artifacts(tmp_path);Path(data['server']).chmod(0o644)
    with pytest.raises(handoff.HandoffError,match='executable'):handoff.read_build(tmp_path)


def test_server_runtime_probe_rejects_loader_failure(tmp_path):
    server=tmp_path/'llama-server';server.write_text('#!/bin/sh\necho loader-error >&2\nexit 127\n');server.chmod(0o755)
    with pytest.raises(RuntimeError,match='runtime_probe_failed'):llama.server_version(server)
    server.write_text('#!/bin/sh\nexit 0\n')
    with pytest.raises(RuntimeError,match='runtime_probe_failed'):llama.server_version(server)
    server.write_text('#!/bin/sh\necho version-test\n')
    assert llama.server_version(server)=='version-test'


def test_smoke_load_reports_effective_context_after_degradation():
    app=SimpleNamespace(manager=SimpleNamespace(ensure=lambda _:SimpleNamespace(ctx_size=4096)),cfg=SimpleNamespace(chat=SimpleNamespace(ctx_size=8192)))
    result=smoke.load_model(app)
    assert result['effective_ctx']==4096


def test_failure_code_never_echoes_raw_secret_or_command():
    assert handoff.failure_code(RuntimeError("Bearer confidential-token --api-key private")) == "unclassified_runtime_failure"
    assert handoff.failure_code(RuntimeError("llama-server が見つかりません。")) == "llama_server_not_found"
    outer=RuntimeError("wrapper");outer.__cause__=handoff.HandoffError("server_hash_mismatch")
    assert handoff.failure_code(outer)=="server_hash_mismatch"


def test_multiline_known_failure_extracts_only_allowlisted_code():
    error=RuntimeError("起動を中止しました:\n- backend_port_in_use_no_process_was_killed")
    assert handoff.failure_code(error)=="backend_port_in_use_no_process_was_killed"
