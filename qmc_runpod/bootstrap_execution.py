"""Disabled SSH cold bootstrap/load boundary. No imports execute remote scripts."""
import json
import shlex

from .c1_ports import PortError, ServerPins, sha
from .execution_adapters import Effect, SSHExecution, bootstrap_script, server_argv
from .production_boundary import EffectBoundary
from .upstream_sse import _strict_json


def _proof(data, effect, pins, binary_sha256=None, *, ready=False):
    try:
        value = _strict_json(data.decode("utf-8"))
        expected = {"session_id", "pod_id", "model_sha256", "llama_commit", "binary_sha256"}
        if ready: expected |= {"pid", "authenticated", "alias", "context"}
        if (type(value) is not dict or set(value) != expected or value["session_id"] != effect.session_id
                or value["pod_id"] != effect.pod_id or value["model_sha256"] != pins.model_sha256
                or value["llama_commit"] != pins.llama_commit): raise ValueError()
        sha(value["binary_sha256"])
        if binary_sha256 is not None and value["binary_sha256"] != binary_sha256: raise ValueError()
        if ready and (type(value["pid"]) is not int or value["pid"] <= 0 or value["authenticated"] is not True
                      or value["alias"] != pins.alias or type(value["context"]) is not int or value["context"] != pins.context): raise ValueError()
        return value
    except (UnicodeError, ValueError, TypeError, KeyError): raise PortError("remote_process_proof_invalid") from None


def load_script(effect, binary_sha256, pins=None):
    pins = pins if pins is not None else ServerPins(); sha(binary_sha256)
    if type(effect) is not Effect or effect.action != "load" or type(pins) is not ServerPins: raise PortError("load_scope_required")
    root = "/workspace/qmc-" + effect.session_id
    command = " ".join(shlex.quote(arg) for arg in server_argv(effect.session_id, pins))
    proof = {"session_id": effect.session_id, "pod_id": effect.pod_id, "model_sha256": pins.model_sha256,
             "llama_commit": pins.llama_commit, "binary_sha256": binary_sha256,
             "alias": pins.alias, "context": pins.context}
    # The remote key is read in Python memory, not interpolated into a command
    # line, returned proof, subprocess log or a provider environment payload.
    return f'''set -eu
umask 077
test "${{RUNPOD_POD_ID:-}}" = '{effect.pod_id}'
cd '{root}'
test -s /run/qmc/model-api-key
test ! -e server.pid
printf '%s  build/bin/llama-server\\n' '{binary_sha256}' | sha256sum --check --status
printf '%s  model.gguf\\n' '{pins.model_sha256}' | sha256sum --check --status
nohup {command} >server.log 2>&1 < /dev/null &
printf '%s\\n' "$!" >server.pid
python3 - <<'QMC_READINESS'
import hashlib,json,pathlib,time,urllib.request,urllib.error
proof=json.loads({json.dumps(json.dumps(proof))})
pid=int(pathlib.Path('server.pid').read_text())
exe=pathlib.Path('/proc')/str(pid)/'exe'
process_deadline=time.monotonic()+5
while time.monotonic()<process_deadline:
    try:
        if hashlib.sha256(exe.read_bytes()).hexdigest()==proof['binary_sha256']: break
    except OSError: pass
    time.sleep(.01)
else: raise SystemExit(1)
args=(pathlib.Path('/proc')/str(pid)/'cmdline').read_bytes().split(b'\\0')
assert b'127.0.0.1' in args and b'8080' in args and b'8192' in args
assert b'--api-key-file' in args and b'/run/qmc/model-api-key' in args
key_path=pathlib.Path('/run/qmc/model-api-key')
assert key_path.stat().st_size<=2048
keys=[value for value in key_path.read_text().splitlines() if value and not value.startswith('#')]
assert len(keys)==1
key=keys[0]
assert 16<=len(key)<=256 and '\\n' not in key and '\\r' not in key
end=time.monotonic()+600
while time.monotonic()<end:
    try:
        req=urllib.request.Request('http://127.0.0.1:8080/v1/models',headers={{'Authorization':'Bearer '+key}})
        # No proxy and no redirection even inside the authenticated Pod.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs): return None
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({{}}),NoRedirect())
        with opener.open(req,timeout=2) as response:
            raw=response.read(8193)
            assert len(raw)<=8192 and response.status==200
        models=json.loads(raw)
        assert any(row.get('id')==proof['alias'] for row in models['data'])
        health=urllib.request.Request('http://127.0.0.1:8080/health',headers={{'Authorization':'Bearer '+key}})
        with opener.open(health,timeout=2) as response:
            assert response.status==200 and len(response.read(8193))<=8192
        try:
            unauthenticated=opener.open('http://127.0.0.1:8080/v1/models',timeout=2)
        except urllib.error.HTTPError as error:
            assert error.code in (401,403)
        else:
            unauthenticated.close(); raise AssertionError('authentication not required')
        assert exe.exists()
        assert hashlib.sha256(exe.read_bytes()).hexdigest()==proof['binary_sha256']
        proof.update(pid=pid,authenticated=True)
        print(json.dumps(proof,sort_keys=True));break
    except Exception: time.sleep(.2)
else: raise SystemExit(1)
QMC_READINESS
'''.encode()


class BootstrapExecution:
    def __init__(self, *, boundary=None, ssh=None, pins=None):
        self.boundary = boundary if boundary is not None else EffectBoundary()
        self.ssh = ssh if ssh is not None else SSHExecution()
        self.pins = pins if pins is not None else ServerPins()
        if type(self.boundary) is not EffectBoundary or type(self.pins) is not ServerPins: raise PortError("typed_bootstrap_boundary_required")

    def bootstrap(self, effect, peer):
        if effect.action != "bootstrap" or effect.pod_id != peer.pod_id: raise PortError("bootstrap_scope_required")
        def invoke(e, cancel, fence):
            fence(); data = self.ssh.run(e, peer, bootstrap_script(e.session_id,e.pod_id,self.pins), cancel)
            proof = _proof(data,e,self.pins); fence(); return proof
        return self.boundary.execute(effect,invoke)

    def load(self, effect, peer, binary_sha256):
        if effect.action != "load" or effect.pod_id != peer.pod_id: raise PortError("load_scope_required")
        def invoke(e, cancel, fence):
            fence(); data = self.ssh.run(e,peer,load_script(e,binary_sha256,self.pins),cancel)
            proof = _proof(data,e,self.pins,binary_sha256,ready=True); fence(); return proof
        return self.boundary.execute(effect,invoke)
