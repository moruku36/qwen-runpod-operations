"""Explicit local durable mock authority; inert until journal.open().

The separately protected head and injected signing key are trust roots. No key
discovery/creation or ACL change occurs. C2 must protect both from untrusted users;
rollback of ledger+head+key by their owner is outside this security model.
"""
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import threading

from .ondemand import (CorruptCheckpoint, MemoryStateStore, MockAuthority, UsageLimits,
                      UsageScope, _limits_dict, _validate_state, _finite_number, ControllerError)
from .c1_ports import Rate
from .upstream_sse import _strict_json


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class AuthorityJournal:
    """One OS owner lock; atomic SQL append + signed independent head CAS.

    Head advances BEFORE append commit. A crash between them fails closed at
    resume. No partially committed record grants an effect; callers reconcile
    rather than resetting/deleting either trust-root file. No network dependency.
    """
    def __init__(self, path, head_path, *, key, domain="c1-public-mock"):
        if type(key) is not bytes or len(key) < 32 or len(key) > 128:
            raise ValueError("injected_signing_key_required")
        self.path, self.head_path = Path(path), Path(head_path)
        if self.path.resolve() == self.head_path.resolve() or type(domain) is not str or not 1 <= len(domain) <= 64:
            raise ValueError("independent_trust_root_required")
        self._key, self.domain = key, domain
        self._lock = threading.RLock()
        self._db = self._owner = None
        self._uses = 0
        self.sequence, self.digest = 0, "0" * 64
        self._latest = None

    def _mac(self, value):
        domain = self.domain + "\0" + str(self.path.resolve()).casefold()
        return hmac.new(self._key, domain.encode() + b"\0" + encode(value), hashlib.sha256).hexdigest()

    def _head(self):
        if self.head_path.stat().st_size > 1024: raise CorruptCheckpoint("head_invalid")
        value = _strict_json(self.head_path.read_text(encoding="utf-8"))
        unsigned = {"sequence": value["sequence"], "digest": value["digest"]}
        if type(unsigned["sequence"]) is not int or unsigned["sequence"] < 0 or not hmac.compare_digest(value["mac"], self._mac(unsigned)):
            raise CorruptCheckpoint("head_invalid")
        return unsigned

    def _advance_head(self, expected, value):
        if self.head_path.exists():
            if self._head() != expected: raise CorruptCheckpoint("head_compare_and_swap_refused")
        elif expected != {"sequence": 0, "digest": "0" * 64}:
            raise CorruptCheckpoint("head_missing")
        payload = encode(dict(value, mac=self._mac(value)))
        temporary = self.head_path.with_name(self.head_path.name + ".pending")
        with temporary.open("wb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, self.head_path)

    @contextmanager
    def open(self):
        # Lifecycle uses the SAME mutex as head reads/appends. Refuse a busy
        # generation instead of waiting behind a cold network/process operation.
        if not self._lock.acquire(False): raise CorruptCheckpoint("journal_in_use")
        try:
            if self._db is not None or self._owner is not None: raise CorruptCheckpoint("journal_already_open")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.head_path.parent.mkdir(parents=True, exist_ok=True)
            owner = open(str(self.path) + ".owner", "a+b")
            try:
                if os.fstat(owner.fileno()).st_size == 0: owner.write(b"\0"); owner.flush()
                owner.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                owner.close(); raise CorruptCheckpoint("single_owner_lock_refused") from None
            self._owner = owner
            self._uses = 1  # Protect even reentrant lifecycle calls during setup.
            try: self._load_open_generation()
            except BaseException:
                if self._db is not None: self._db.close(); self._db = None
                owner.close(); self._owner = None
                raise
            finally: self._uses = 0
        finally: self._lock.release()
        try: yield self
        finally: self.close(expected_owner=owner)

    def _load_open_generation(self):
        fresh = not self.path.exists()
        if not fresh and self.path.stat().st_size > 33554432: raise CorruptCheckpoint("journal_bound_exceeded")
        self._db = sqlite3.connect(self.path, timeout=0, check_same_thread=False)
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("CREATE TABLE IF NOT EXISTS journal (sequence INTEGER PRIMARY KEY, previous TEXT NOT NULL, payload BLOB NOT NULL, digest TEXT NOT NULL)")
        self._db.commit()
        previous = "0" * 64; sequence = 0; latest = None
        for number, prior, raw, digest in self._db.execute("SELECT sequence,previous,payload,digest FROM journal ORDER BY sequence"):
            if len(raw) > 1048576 or number != sequence + 1 or prior != previous:
                raise CorruptCheckpoint("journal_chain_invalid")
            data = _strict_json(raw.decode())
            if not hmac.compare_digest(digest, self._mac({"sequence": number, "previous": prior, "data": data})):
                raise CorruptCheckpoint("journal_integrity_refused")
            sequence, previous, latest = number, digest, data
            if sequence > 10000: raise CorruptCheckpoint("journal_bound_exceeded")
        expected = {"sequence": sequence, "digest": previous}
        if not self.head_path.exists():
            if not fresh or sequence: raise CorruptCheckpoint("head_missing")
            self._advance_head(expected, expected)
        if self._head() != expected: raise CorruptCheckpoint("journal_rollback_or_incomplete_commit")
        self.sequence, self.digest, self._latest = sequence, previous, latest

    def close(self, *, expected_owner=None):
        """Close only a quiescent generation; busy refusal retains DB/OS lock.

        If open-context exit encounters active work, cancel/join the owning host
        and explicitly retry close(). Reopen cannot silently steal its generation.
        """
        if not self._lock.acquire(False): raise CorruptCheckpoint("journal_in_use")
        try:
            if self._owner is None and self._db is None: return
            if expected_owner is not None and self._owner is not expected_owner:
                raise CorruptCheckpoint("journal_generation_stale")
            if self._uses: raise CorruptCheckpoint("journal_in_use")
            try:
                if self._db is not None: self._db.close(); self._db = None
            finally:
                if self._owner is not None: self._owner.close(); self._owner = None
        finally: self._lock.release()

    @contextmanager
    def use(self, expected_owner):
        """Pin an exact open generation without holding a mutex over I/O."""
        with self._lock:
            if expected_owner is None or self._owner is not expected_owner or self._db is None:
                raise CorruptCheckpoint("journal_generation_stale")
            self._uses += 1
        try: yield
        finally:
            with self._lock:
                if self._owner is not expected_owner: raise CorruptCheckpoint("journal_generation_stale")
                self._uses -= 1

    def latest(self):
        with self._lock:
            if self._db is None: raise CorruptCheckpoint("journal_closed")
            return None if self._latest is None else _strict_json(encode(self._latest).decode())

    def append(self, data):
        with self._lock:
            if self._db is None: raise CorruptCheckpoint("journal_closed_or_full")
            with self.use(self._owner): return self._append_open_generation(data)

    def _append_open_generation(self, data):
        with self._lock:
            if self._db is None or self.sequence >= 10000: raise CorruptCheckpoint("journal_closed_or_full")
            if self.path.stat().st_size > 33554432: raise CorruptCheckpoint("journal_bound_exceeded")
            if self._head() != {"sequence": self.sequence, "digest": self.digest}:
                raise CorruptCheckpoint("head_compare_and_swap_refused")
            raw = encode(data)
            if len(raw) > 1048576: raise CorruptCheckpoint("journal_record_too_large")
            if self._latest is not None and raw == encode(self._latest): return
            sequence = self.sequence + 1
            digest = self._mac({"sequence": sequence, "previous": self.digest, "data": data})
            self._advance_head({"sequence": self.sequence, "digest": self.digest}, {"sequence": sequence, "digest": digest})
            try:
                self._db.execute("INSERT INTO journal VALUES(?,?,?,?)", (sequence, self.digest, raw, digest))
                self._db.commit()
            except Exception:
                self._db.rollback(); raise CorruptCheckpoint("journal_commit_unknown") from None
            self.sequence, self.digest, self._latest = sequence, digest, _strict_json(raw.decode())


class MeteredMockAuthority(MockAuthority):
    def __init__(self, rate):
        super().__init__()
        if type(rate) is not Rate: raise ValueError("typed_rate_required")
        self.rate = rate

    def check_effect(self, session_id, operation, action, now):
        with self._lock:
            now = max(self._high_water, _finite_number(now, "clock"))
            super().check_effect(session_id, operation, action, now)
            record = self._record(session_id); limits = record["limits"]
            increment = Decimal("0") if operation in record["effects"] else {
                "chat": Decimal(".01"), "export": Decimal(".0001"), "terminate": Decimal(".0001")}.get(action, Decimal("0"))
            cap = limits.max_usd if action in {"export", "terminate"} else limits.max_usd - limits.cleanup_usd
            total = self.rate.cost(record["started_at"], now) + record["work_spent"] + record["cleanup_spent"] + increment
            if total > cap: raise ControllerError("elapsed_rate_budget_expired")


class DurableMockAuthority(MeteredMockAuthority):
    """Original trusted ledger, made durable at every seal before mock effects."""
    def __init__(self, journal, rate=None):
        super().__init__(rate if rate is not None else Rate())
        if type(journal) is not AuthorityJournal: raise ValueError("journal_required")
        self.journal = journal
        self.capture = lambda state: {}

    def dump(self):
        records = {}
        for sid, record in self._records.items():
            records[sid] = dict(record, limits=_limits_dict(record["limits"]),
                                work_spent=str(record["work_spent"]), cleanup_spent=str(record["cleanup_spent"]))
        return {"records": records, "used": self._used_approvals, "closed": self._closed,
                "high_water": self._high_water, "digest": self._digest}

    def seal(self, state):
        with self._lock:
            super().seal(state)
            self.journal.append({"state": state, "authority": self.dump(), "aux": self.capture(state)})

    def restore(self):
        data = self.journal.latest()
        if data is None: return MemoryStateStore(), {}
        state = _validate_state(data["state"]); ledger = data["authority"]
        records = {}
        for sid, row in ledger["records"].items():
            values = row["limits"]
            limits = UsageLimits.create(max_runtime_seconds=values["max_runtime_seconds"], max_usd=values["max_usd"],
                expires_at=values["expires_at"], scope=UsageScope(sid, values["target"], frozenset(values["actions"])),
                cleanup_runtime_seconds=values["cleanup_runtime_seconds"], cleanup_usd=values["cleanup_usd"])
            records[sid] = dict(row, limits=limits, work_spent=Decimal(row["work_spent"]), cleanup_spent=Decimal(row["cleanup_spent"]))
        self._records, self._used_approvals, self._closed = records, ledger["used"], ledger["closed"]
        self._high_water, self._digest = ledger["high_water"], ledger["digest"]
        self.check(state)
        return MemoryStateStore(encode(state).decode()), data["aux"]
