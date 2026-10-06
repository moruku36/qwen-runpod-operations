"""Offline regression coverage for stage ordering and subprocess activation."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import pytest
from qmc_runpod import envsetup,stages,toolchain


def test_net_toolchain_failure_precedes_all_network_calls(tmp_path,monkeypatch):
    r=stages.Runner(tmp_path,mirror=None)
    def fail(r): raise RuntimeError('toolchain missing: cmake ninja')
    monkeypatch.setattr(toolchain,'prepare',fail)
    r.run=lambda *a,**kw: pytest.fail('network command must not execute')
    with pytest.raises(RuntimeError,match='toolchain missing'):
        stages.stage_net(r,opener=lambda *a,**kw:pytest.fail('HTTP must not execute'))


@pytest.mark.parametrize('stage',['source','venv'])
def test_direct_stage_refuses_absent_toolchain_receipt(tmp_path,monkeypatch,stage):
    r=stages.Runner(tmp_path,mirror=None)
    r.run=lambda *a,**kw:pytest.fail('clone or pip must not execute')
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a,0,stdout='3.11'))
    def missing(root):raise RuntimeError('toolchain receipt missing')
    monkeypatch.setattr(toolchain,'assert_ready',missing)
    with pytest.raises(RuntimeError,match='receipt missing'):
        if stage=='source':stages.stage_source(r)
        else:stages.stage_venv(r,Path('never-read.lock'))


def test_activation_env_preserves_parent_and_sets_tools_first(tmp_path):
    original={'PATH':'/usr/bin:/bin','SOME_VALUE':'preserved','PYTHONHOME':'wrong','PYTHONPATH':'stale/package'}
    env=envsetup.activation_env(tmp_path,original)
    assert env['PATH'].split(os.pathsep)[0]==str(tmp_path/'venv/bin')
    assert env['VIRTUAL_ENV']==str(tmp_path/'venv')
    assert env['PYTHONNOUSERSITE']=='1' and 'PYTHONHOME' not in env and 'PYTHONPATH' not in env
    assert env['SOME_VALUE']=='preserved' and original['PYTHONHOME']=='wrong'
    assert envsetup.activation_env(tmp_path,env)['PATH']==env['PATH']


def test_cli_reexec_transmits_venv_path(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('cli_under_test',Path(__file__).parents[1]/'scripts/pod_run.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    py=envsetup.venv_python(tmp_path);py.parent.mkdir(parents=True);py.write_text('')
    monkeypatch.setattr(envsetup,'running_in_venv',lambda root:False)
    captured={}
    class Executed(Exception):pass
    def execve(path,args,env):
        captured.update(path=path,args=args,env=env);raise Executed()
    monkeypatch.setattr(os,'execve',execve)
    with pytest.raises(Executed):cli._reexec_in_venv(tmp_path)
    assert captured['path']==str(py)
    assert captured['env']['PATH'].split(os.pathsep)[0]==str(py.parent)
