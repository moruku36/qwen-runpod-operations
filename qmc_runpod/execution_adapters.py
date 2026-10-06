"""Explicitly injected, disabled-by-default provider and SSH execution ports.

No constructor discovers a secret, opens a connection or starts a process.
Production activation is a service-side capability, never a request JSON flag.
This module is not installed or activated by importing it.
"""
from dataclasses import dataclass, field
from decimal import Decimal
import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import math
from contextlib import contextmanager
from pathlib import Path
import re
import socket
import ssl
import subprocess
import threading
import time

from .c1_ports import PortError, PodSpec, ServerPins, identity, sha
from .upstream_sse import _Response, _strict_json


@dataclass(frozen=True)
class Effect:
    session_id: str
    operation_id: str
    action: str
    pod_id: str | None
    deadline: float

    def __post_init__(self):
        identity(self.session_id); sha(self.operation_id)
        if self.action not in {"create", "read", "terminate", "bootstrap", "load"}: raise PortError("invalid_effect")
        if self.pod_id is not None:
            identity(self.pod_id)
            if self.pod_id == "synthetic-held-pod": raise PortError("retained_pod_excluded")
        if type(self.deadline) not in (int, float) or not math.isfinite(self.deadline): raise PortError("invalid_deadline")


class ExecutionGate:
    """Trusted injected control callback must recheck durable approval every gate.

    Missing callback denies before credentials, sockets or processes are touched.
    The callback cannot be supplied by an HTTP caller or recovered from a ledger.
    Cancellation/deadline are checked again after it returns.
    """
    def __init__(self, check=None): self._check = check

    def check(self, effect, cancel):
        if type(effect) is not Effect or not isinstance(cancel, threading.Event): raise PortError("typed_effect_required")
        if self._check is None: raise PortError("execution_disabled")
        if cancel.is_set() or time.monotonic() >= effect.deadline: raise PortError("effect_cancelled_or_expired")
        if self._check(effect) is not True: raise PortError("execution_not_approved")
        if cancel.is_set() or time.monotonic() >= effect.deadline: raise PortError("effect_cancelled_or_expired")


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, address, timeout):
        super().__init__("rest.runpod.io", 443, timeout=timeout, context=ssl.create_default_context())
        self.address = address
        self.response_class = _Response

    def connect(self):
        # Explicit C2-resolved IP avoids proxy, redirection and unbounded DNS.
        raw = socket.create_connection((self.address, 443), self.timeout)
        try: self.sock = self._context.wrap_socket(raw, server_hostname="rest.runpod.io")
        except BaseException: raw.close(); raise


class RunPodREST:
    """One bounded TLS request, no retry/list-all/name-based reconciliation.

    The exact outbound IP and bearer are injected only after approved activation.
    Errors never retain response bodies, authorization values or raw exceptions.
    """
    def __init__(self, *, gate=None, address=None, bearer=None):
        self.gate = gate if gate is not None else ExecutionGate()
        if type(self.gate) is not ExecutionGate: raise PortError("execution_gate_required")
        self._address, self._bearer = address, bearer

    def __repr__(self): return "RunPodREST(disabled_or_explicitly_injected)"

    def request(self, effect, method, path, body, cancel):
        self.gate.check(effect, cancel)
        expected = {"create": ("POST", "/pods"), "read": ("GET", "/pods/" + str(effect.pod_id)),
                    "terminate": ("DELETE", "/pods/" + str(effect.pod_id))}.get(effect.action)
        if expected != (method, path) or (effect.action != "create" and effect.pod_id is None): raise PortError("request_scope_mismatch")
        if type(body) is not bytes or len(body) > 16384: raise PortError("request_body_bound")
        try:
            address = ipaddress.ip_address(self._address)
            if not address.is_global: raise ValueError()
            if type(self._bearer) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{16,512}", self._bearer): raise ValueError()
        except (ValueError, TypeError): raise PortError("approved_endpoint_and_bearer_required") from None
        connection = _PinnedHTTPS(str(address), min(2, max(.001, effect.deadline-time.monotonic())))
        done = threading.Event()
        owned_socket = [None]
        def watch():
            while not done.wait(.01):
                if cancel.is_set() or time.monotonic() >= effect.deadline:
                    sock = owned_socket[0] or connection.sock
                    if sock is not None:
                        try: sock.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
                    return
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            self.gate.check(effect, cancel)
            connection.connect()
            owned_socket[0] = connection.sock
            self.gate.check(effect, cancel)
            connection.request(method, "/v1" + path, body=body if method == "POST" else None,
                headers={"Authorization": "Bearer " + self._bearer, "Content-Type": "application/json", "Connection": "close"})
            response = connection.getresponse()
            self.gate.check(effect, cancel)
            if response.status == 404 and effect.action == "read": return None
            if response.status not in ({200, 201} if effect.action == "create" else {200, 204}): raise PortError("provider_request_rejected")
            data = bytearray()
            while True:
                self.gate.check(effect, cancel)
                chunk = response.read(4096)
                self.gate.check(effect, cancel)
                if not chunk: break
                data.extend(chunk)
                if len(data) > 131072: raise PortError("provider_response_bound")
            if effect.action == "terminate": return True
            value = _strict_json(bytes(data).decode("utf-8"))
            if type(value) is not dict: raise PortError("provider_response_invalid")
            self.gate.check(effect, cancel)
            return value
        except Exception:
            # Create/DELETE transport failure is UNKNOWN, not absence or permission
            # to replay. The coordinator already persisted its one-attempt intent.
            raise PortError("provider_outcome_unknown") from None
        finally:
            done.set(); connection.close(); watcher.join(1)


def validate_pod(value, spec, session_id, *, pod_id=None, max_gpu_usd_per_hour=Decimal("1.80")):
    """Strip provider env/unrelated fields; a matching name alone is never proof."""
    if type(value) is not dict or type(spec) is not PodSpec: raise PortError("pod_proof_invalid")
    identity(session_id); resource = identity(value.get("id"))
    if resource == "synthetic-held-pod" or (pod_id is not None and resource != pod_id): raise PortError("pod_proof_invalid")
    name = "qmc-" + hashlib.sha256(session_id.encode()).hexdigest()[:24]
    try:
        if (type(value["costPerHr"]) is not str or type(value["gpu"]["count"]) is not int
                or type(value["volumeInGb"]) is not int or type(value["containerDiskInGb"]) is not int): raise ValueError()
        price = Decimal(value["costPerHr"])
        if (value["consumerUserId"] != spec.account or value["name"] != name or value["image"] != spec.image
                or value["interruptible"] is not False or value["gpu"]["count"] != 1
                or value["volumeInGb"] != spec.volume_gb or value["containerDiskInGb"] != spec.container_gb
                or value.get("networkVolume") is not None or not price.is_finite()
                or price <= 0 or price > max_gpu_usd_per_hour): raise ValueError()
        # Only deployment-reviewed GPU type evidence qualifies; count alone cannot.
        if value["machine"]["gpuTypeId"] != spec.gpu_type: raise ValueError()
    except (KeyError, TypeError, ValueError, ArithmeticError): raise PortError("pod_proof_invalid") from None
    return {"id": resource, "account": spec.account, "cost_per_hour": str(price)}


@dataclass(frozen=True)
class SSHPeer:
    pod_id: str
    address: str
    port: int
    known_hosts: str = field(repr=False)
    private_key: str = field(repr=False)
    fingerprint: str

    def __post_init__(self):
        identity(self.pod_id)
        if self.pod_id == "synthetic-held-pod": raise PortError("retained_pod_excluded")
        try:
            if not ipaddress.ip_address(self.address).is_global: raise ValueError()
        except ValueError: raise PortError("ssh_literal_public_address_required") from None
        if type(self.port) is not int or not 1 <= self.port <= 65535: raise PortError("ssh_port_invalid")
        if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", self.fingerprint): raise PortError("verified_host_key_required")
        for path in (self.known_hosts, self.private_key):
            if type(path) is not str or not Path(path).is_absolute() or any(c in path for c in "\r\n\0"): raise PortError("explicit_ssh_paths_required")


def ssh_argv(peer, *, forward=False):
    if type(peer) is not SSHPeer: raise PortError("ssh_peer_required")
    argv = ["ssh", "-F", "none", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + peer.known_hosts,
            "-o", "GlobalKnownHostsFile=none", "-o", "ExitOnForwardFailure=yes",
            "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=1",
            "-i", peer.private_key, "-p", str(peer.port)]
    if forward: argv += ["-N", "-L", "127.0.0.1:19181:127.0.0.1:8080"]
    argv += ["root@" + peer.address]
    if not forward: argv += ["sh", "-s"]
    return tuple(argv)


def verify_known_host(peer, data):
    if type(peer) is not SSHPeer or type(data) is not bytes or len(data)>4096: raise PortError("host_key_not_proven")
    try:
        lines=data.decode("ascii").splitlines()
        if len(lines)!=1: raise ValueError()
        host, kind, encoded=lines[0].split()
        expected=peer.address if peer.port==22 else "["+peer.address+"]:"+str(peer.port)
        if host!=expected or kind not in {"ssh-ed25519","ssh-rsa","ecdsa-sha2-nistp256"}: raise ValueError()
        key=base64.b64decode(encoded,validate=True)
        if not 16<=len(key)<=2048: raise ValueError()
        fingerprint="SHA256:"+base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip("=")
        if not hmac.compare_digest(fingerprint,peer.fingerprint): raise ValueError()
    except (ValueError,UnicodeError): raise PortError("host_key_not_proven") from None


class SSHExecution:
    """Finite bootstrap/load process. A separate retained tunnel handle is required.

    No credential file is read here. C2 must supply a dedicated known_hosts file
    containing only the independently verified peer key, plus an explicit key.
    stderr is suppressed and stdout bounded. Only the owned child is stopped.
    """
    def __init__(self, gate=None, *, executable=None):
        self.gate = gate if gate is not None else ExecutionGate()
        self._executable=executable

    def _argv(self, peer, *, forward=False):
        if type(self._executable) is not str or not Path(self._executable).is_absolute():
            raise PortError("approved_ssh_executable_required")
        # known_hosts is public key data, never the private key. No discovery.
        with Path(peer.known_hosts).open("rb") as stream: data=stream.read(4097)
        verify_known_host(peer,data)
        return (self._executable,*ssh_argv(peer,forward=forward)[1:])

    def run(self, effect, peer, script, cancel):
        self.gate.check(effect, cancel)
        if effect.action not in {"bootstrap", "load"} or effect.pod_id != peer.pod_id: raise PortError("ssh_scope_mismatch")
        if type(script) is not bytes or len(script) > 32768: raise PortError("bootstrap_script_bound")
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        argv=self._argv(peer)
        self.gate.check(effect, cancel)
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, shell=False, creationflags=flags)
        result, failure = bytearray(), []
        done = threading.Event()
        def watch():
            while not done.wait(.01):
                if cancel.is_set() or time.monotonic() >= effect.deadline:
                    if process.poll() is None:
                        try: process.terminate()
                        except OSError: pass
                    return
        watcher = threading.Thread(target=watch, daemon=True); watcher.start()
        def read():
            try:
                while True:
                    chunk = process.stdout.read(1024)
                    if not chunk: return
                    result.extend(chunk)
                    if len(result) > 8192: failure.append(True); return
            except Exception: failure.append(True)
        reader = threading.Thread(target=read, daemon=True); reader.start()
        try:
            self.gate.check(effect,cancel)
            process.stdin.write(script); process.stdin.close()
            while process.poll() is None:
                self.gate.check(effect, cancel)
                if failure: raise PortError("bootstrap_response_bound")
                cancel.wait(.01)
            reader.join(1); self.gate.check(effect, cancel)
            if reader.is_alive() or failure or process.returncode != 0: raise PortError("bootstrap_failed")
            return bytes(result)
        except Exception: raise PortError("bootstrap_outcome_unknown") from None
        finally:
            done.set()
            if process.poll() is None:
                process.terminate()
                try: process.wait(1)
                except subprocess.TimeoutExpired: process.kill(); process.wait(1)
            process.stdout.close(); reader.join(1); watcher.join(1)

    @contextmanager
    def tunnel(self, effect, peer, cancel):
        self.gate.check(effect, cancel)
        if effect.action != "load" or effect.pod_id != peer.pod_id: raise PortError("ssh_scope_mismatch")
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        argv=self._argv(peer,forward=True)
        self.gate.check(effect,cancel)
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False, creationflags=flags)
        done = threading.Event()
        def watch():
            while not done.wait(.01):
                try: self.gate.check(effect, cancel)
                except Exception:
                    if process.poll() is None:
                        try: process.terminate()
                        except OSError: pass
                    return
        watcher = threading.Thread(target=watch, daemon=True); watcher.start()
        try:
            self.gate.check(effect, cancel)
            if process.poll() is not None: raise PortError("tunnel_failed")
            # This only proves an owned child, not readiness. Caller must verify
            # forward establishment and authenticated server/process/model proof.
            yield process
        finally:
            done.set()
            if process.poll() is None:
                process.terminate()
                try: process.wait(1)
                except subprocess.TimeoutExpired: process.kill(); process.wait(1)
            watcher.join(1)


def bootstrap_script(session_id, pod_id, pins=None):
    """Immutable text-only cold build plan; returned bytes are never auto-executed.

    Secret file provisioning and its protection are separate approved C2 inputs.
    Cold deadline comes from the absolute grant, not READY inference's 30s budget.
    """
    identity(session_id); identity(pod_id)
    if pod_id == "synthetic-held-pod": raise PortError("retained_pod_excluded")
    pins = pins if pins is not None else ServerPins()
    if type(pins) is not ServerPins: raise PortError("typed_pins_required")
    url = "https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/resolve/" + pins.model_revision + "/" + pins.model_file
    return f'''set -eu
umask 077
test "${{RUNPOD_POD_ID:-}}" = '{pod_id}'
test -s /run/qmc/model-api-key
test ! -e /workspace/qmc-{session_id}
mkdir /workspace/qmc-{session_id}
cd /workspace/qmc-{session_id}
git init -q llama
git -C llama remote add origin https://github.com/ggml-org/llama.cpp.git
git -C llama fetch -q --depth 1 origin {pins.llama_commit}
git -C llama checkout -q --detach FETCH_HEAD
test "$(git -C llama rev-parse HEAD)" = '{pins.llama_commit}'
cmake -S llama -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=80 -DLLAMA_CURL=OFF >build.log 2>&1
cmake --build build --target llama-server -j 8 >>build.log 2>&1
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --tlsv1.2 --max-time 1800 --output model.gguf '{url}'
printf '%s  model.gguf\\n' '{pins.model_sha256}' | sha256sum --check --status
binary_sha=$(sha256sum build/bin/llama-server | cut -d ' ' -f 1)
printf '{{"session_id":"{session_id}","pod_id":"{pod_id}","model_sha256":"{pins.model_sha256}","llama_commit":"{pins.llama_commit}","binary_sha256":"%s"}}\\n' "$binary_sha"
'''.encode()


def server_argv(session_id, pins=None):
    identity(session_id); pins = pins if pins is not None else ServerPins()
    if type(pins) is not ServerPins: raise PortError("typed_pins_required")
    root = "/workspace/qmc-" + session_id
    return (root + "/build/bin/llama-server", "--model", root + "/model.gguf", "--alias", pins.alias,
            "--ctx-size", "8192", "--n-gpu-layers", "99", "--host", "127.0.0.1", "--port", "8080",
            "--api-key-file", "/run/qmc/model-api-key", "--no-webui", "--no-warmup")
