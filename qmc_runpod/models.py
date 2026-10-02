"""Download the two model files at pinned revisions and verify SHA256 before use."""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import pins


class VerifyError(RuntimeError):
    pass


def sha256_file(path: Path, chunk: int = 16 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def verify(path: Path, spec: dict) -> dict:
    """Check size (when known) and SHA256 of ``path``. Raises VerifyError on mismatch."""
    size = path.stat().st_size
    if spec.get("size_bytes") and size != spec["size_bytes"]:
        raise VerifyError(f"{spec['file']}: size {size} != {spec['size_bytes']}")
    digest = sha256_file(path)
    if digest != spec["sha256"]:
        raise VerifyError(f"{spec['file']}: sha256 mismatch (got {digest[:12]}…, want {spec['sha256'][:12]}…)")
    return {"file": spec["file"], "revision": spec["revision"], "sha256_verified": True, "size_bytes": size}


def fetch_pinned(cache_dir: Path, *, download=None, specs: dict | None = None):
    """Return ((chat_path, mmproj_path), verification_records). ``download`` is injectable for tests."""
    specs = specs or pins.MODELS
    if download is None:
        from huggingface_hub import hf_hub_download

        def download(repo, filename, revision, cache_dir):
            return hf_hub_download(repo_id=repo, filename=filename, revision=revision, cache_dir=str(cache_dir))

    paths, records = {}, []
    for role in ("chat", "mmproj"):  # explicit files only; never a whole-repo snapshot
        spec = specs[role]
        p = Path(download(spec["repo"], spec["file"], spec["revision"], cache_dir))
        records.append({"role": role, **verify(p, spec)})
        paths[role] = p
    return (paths["chat"], paths["mmproj"]), records
