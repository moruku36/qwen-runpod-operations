"""Offline OWUI0.11.4 patch planner. No installed file/config/DB reads or writes.

Input is explicitly supplied nonsecret backend source. Output is a disabled staged
review bundle with exact originals, hashes and rollback bytes; never activation.
"""
import ast
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path

from .c1_ports import PortError, sha


HOOK = '''# qmc hook starts disabled; runtime/controller injection is explicit C2 work.
from fastapi import HTTPException

class DisabledDispatcher:
    def begin(self, request, user):
        raise HTTPException(status_code=503, detail="On-demand integration disabled")
    async def final_chat(self, request, form_data, user, context=None):
        raise HTTPException(status_code=503, detail="On-demand integration disabled")

dispatcher = DisabledDispatcher()
'''


@dataclass(frozen=True)
class InstallPlan:
    original_sha256: str
    patched_sha256: str
    original: bytes = field(repr=False)
    patched: bytes = field(repr=False)
    hook: bytes = field(repr=False)

    def stage(self, directory):
        # Only a new review directory: never overwrite a checkout/installed app.
        directory = Path(directory)
        if not directory.is_absolute() or directory.exists(): raise PortError("new_absolute_staging_directory_required")
        directory.mkdir(parents=True)
        (directory / "original-main.py").write_bytes(self.original)
        (directory / "patched-main.py").write_bytes(self.patched)
        (directory / "qmc_ondemand_hook.py").write_bytes(self.hook)
        (directory / "manifest.json").write_text(json.dumps({"version": "0.11.4", "enabled": False,
            "original_sha256": self.original_sha256, "patched_sha256": self.patched_sha256,
            "hook_sha256": hashlib.sha256(self.hook).hexdigest(), "configuration_changes": [],
            "installation_performed": False, "rollback": "Restore exact original-main.py using same-handle hash verification and no-replace rename; retain the verified hook by no-replace rename. Unknown files require review."}, indent=2))
        return directory


def build_plan(main_source, *, version, expected_sha256):
    if version != "0.11.4" or type(main_source) is not bytes or len(main_source) > 1048576: raise PortError("pinned_backend_source_required")
    sha(expected_sha256)
    if hashlib.sha256(main_source).hexdigest() != expected_sha256: raise PortError("source_compare_and_swap_failed")
    try: source = main_source.decode("utf-8"); tree = ast.parse(source)
    except (UnicodeError, SyntaxError): raise PortError("backend_source_invalid") from None
    if "qmc_ondemand" in source: raise PortError("already_patched_or_local_conflict")
    functions = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "chat_completion"]
    if len(functions) != 1: raise PortError("manual_entry_not_unique")
    entry = functions[0]
    names = [n.arg for n in entry.args.args]
    if (names != ["request", "form_data", "user"] or len(entry.args.defaults) != 1
            or entry.args.posonlyargs or entry.args.kwonlyargs or entry.args.vararg or entry.args.kwarg): raise PortError("manual_entry_shape_changed")
    user_default = entry.args.defaults[-1]
    if ast.unparse(user_default) != "Depends(get_verified_user)": raise PortError("verified_user_dependency_required")
    decorators = [ast.unparse(n) for n in entry.decorator_list]
    if set(decorators) != {"app.post('/api/chat/completions')", "app.post('/api/v1/chat/completions')"}: raise PortError("manual_routes_changed")
    # Preserve every existing byte except precisely identified call-site spans.
    lines = source.splitlines(keepends=True)
    first = min(n.lineno for n in entry.decorator_list)-1
    signature = "".join(lines[entry.lineno-1:entry.body[0].lineno-1])
    changed_signature = signature.replace("async def chat_completion(", "async def qmc_original_chat_completion(", 1)
    anchor = "    user=Depends(get_verified_user),\n"
    if changed_signature.count(anchor) != 1: raise PortError("manual_signature_changed")
    changed_signature = changed_signature.replace(anchor, anchor + "    *, qmc_context=None,\n")
    calls = [n for n in ast.walk(entry) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "chat_completion_handler"]
    if len(calls) != 1: raise PortError("final_dispatch_not_unique")
    call = calls[0]
    call_line = lines[call.lineno-1]
    anchor = "response = await chat_completion_handler(request, form_data, user)"
    if call_line.strip() != anchor: raise PortError("final_dispatch_shape_changed")
    indent = call_line[:len(call_line)-len(call_line.lstrip())]
    replacement = (indent + "if form_data.get('model') == 'qwen-27b':\n" + indent + "    response = await qmc_ondemand_dispatcher.final_chat(request, form_data, user, context=qmc_context)\n"
        + indent + "else:\n" + indent + "    " + anchor + "\n")
    edits = [(first, entry.body[0].lineno-1, changed_signature), (call.lineno-1, call.lineno, replacement)]
    for start, end, text in sorted(edits, reverse=True): lines[start:end] = [text]
    patched = "".join(lines)
    # Internal/automation entry points retain the original pipeline with no
    # context. Background model reuse therefore reaches the disabled denial.
    state_anchor = "app.state.CHAT_COMPLETION_HANDLER = chat_completion"
    if patched.count(state_anchor) != 1: raise PortError("internal_dispatch_shape_changed")
    patched = patched.replace(state_anchor, "app.state.CHAT_COMPLETION_HANDLER = qmc_original_chat_completion")
    # Legacy/internal aliases never issue a manual intent. Keep their original
    # pipeline, which has no registered context and denies on-demand dispatch.
    for name in ("generate_chat_completions", "generate_chat_completion"):
        anchor = name + " = chat_completion"
        if patched.count(anchor) != 1: raise PortError("legacy_alias_shape_changed")
        patched = patched.replace(anchor, name + " = qmc_original_chat_completion")
    # Preserve the exact original public signature (including Depends), with no
    # new HTTP query/body parameter. Context belongs only to the internal helper.
    wrapper = "\nfrom open_webui.qmc_ondemand_hook import dispatcher as qmc_ondemand_dispatcher\n\n"
    wrapper += "@app.post('/api/chat/completions')\n@app.post('/api/v1/chat/completions')\n"
    wrapper += signature
    wrapper += "    qmc_context = None\n"
    wrapper += "    if form_data.get('model') == 'qwen-27b':\n"
    wrapper += "        qmc_context = qmc_ondemand_dispatcher.begin(request, user)\n"
    wrapper += "    return await qmc_original_chat_completion(request, form_data, user, qmc_context=qmc_context)\n\n"
    # Execute the wrapper definition immediately after the renamed original,
    # before every following module-level use. AST parse/compile alone cannot
    # detect a NameError from a use that precedes the replacement definition.
    edited = ast.parse(patched)
    originals = [n for n in edited.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "qmc_original_chat_completion"]
    if len(originals) != 1: raise PortError("renamed_entry_not_unique")
    insertion = sum(len(line) for line in patched.splitlines(keepends=True)[:originals[0].end_lineno])
    patched = patched[:insertion] + wrapper + patched[insertion:]
    ast.parse(patched)
    data = patched.encode()
    return InstallPlan(expected_sha256, hashlib.sha256(data).hexdigest(), main_source, data, HOOK.encode())
