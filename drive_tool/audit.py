"""Redacted audit records for destructive-safe service operations (milestone 2).

Two small pieces:

* :class:`AuditLog` -- append-only JSON-lines log with a hard size cap. Each
  record is exactly one line written by a single ``os.write`` on a
  ``O_WRONLY|O_APPEND|O_CREAT`` descriptor created with mode ``0o600``.
* :class:`AuditedService` -- a transparent proxy around a
  :class:`~drive_tool.file_service_move.MovableFileService` that records one
  redacted line for ``save``, ``move`` and ``trash`` and passes everything
  else through untouched.

Nothing secret is ever written: the bearer token, the file payload and any
exception text never reach the log. A logging failure never changes the
result of the wrapped operation; it is swallowed and counted in
:attr:`AuditedService.audit_failures`.

Standard library only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

from drive_tool.file_service import ServiceError
from drive_tool.file_service_move import MovableFileService
from drive_tool.service_policy import AgentPolicy

__all__ = ["AuditLog", "AuditedService"]

_MAX_LINE_BYTES = 4096
_MAX_STRING_CHARS = 1024

class AuditLog:
    """Size-capped, thread-safe append-only JSON-lines audit log."""

    __slots__ = ("_path", "_max_bytes", "_lock")

    def __init__(self, path: str, *, max_bytes: int = 1048576) -> None:
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a nonempty str")
        if type(max_bytes) is bool or not isinstance(max_bytes, int):
            raise TypeError("max_bytes must be int")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self._path = path
        self._max_bytes = max_bytes
        self._lock = threading.Lock()

    @property
    def path(self) -> str:
        return self._path

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def record(self, **fields: Any) -> None:
        """Append one JSON line for ``fields`` (a ``ts`` is added)."""
        payload = dict(fields)
        payload["ts"] = (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        line = _encode_line(payload)
        with self._lock:
            self._append(line)

    def _append(self, line: bytes) -> None:
        size = 0
        try:
            size = os.stat(self._path).st_size
        except FileNotFoundError:
            size = 0
        except OSError:
            size = 0

        if size > 0 and size + len(line) > self._max_bytes:
            try:
                os.replace(self._path, self._path + ".1")
            except OSError:
                pass

        fd = os.open(
            self._path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
        try:
            os.write(fd, line)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

def _encode_line(payload: dict[str, Any]) -> bytes:
    """Serialize ``payload`` to one JSON line, bounded at 4096 bytes.

    String field values are truncated until the encoded line fits. Lists
    (``skipped``) are dropped rather than split. If truncation alone cannot
    fit, the line is still emitted whole -- it is never split.
    """
    encoded = _dumps(payload)
    if len(encoded) <= _MAX_LINE_BYTES:
        return encoded

    working = dict(payload)
    for key, value in list(working.items()):
        if isinstance(value, str) and len(value) > _MAX_STRING_CHARS:
            working[key] = value[:_MAX_STRING_CHARS]

    while len(_dumps(working)) > _MAX_LINE_BYTES:
        longest_key = None
        longest_len = 0
        for key, value in working.items():
            if key == "ts":
                continue
            if isinstance(value, str) and len(value) > longest_len:
                longest_key = key
                longest_len = len(value)
        if longest_key is None:
            break
        working[longest_key] = working[longest_key][: max(0, longest_len // 2)]

    encoded = _dumps(working)
    if len(encoded) > _MAX_LINE_BYTES:
        # Bound in the worst case by shrinking the fixed skeleton too.
        minimal = {
            key: (value if isinstance(value, (int, bool)) or value is None else "")
            for key, value in payload.items()
        }
        minimal["ts"] = payload.get("ts", "")
        encoded = _dumps(minimal)
    return encoded

def _dumps(payload: dict[str, Any]) -> bytes:
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return text.encode("utf-8") + b"\n"

class AuditedService:
    """Transparent audit-recording wrapper around a MovableFileService."""

    __slots__ = ("_service", "_policies", "_log", "_failures", "_lock")

    def __init__(
        self,
        service: MovableFileService,
        policies: tuple[AgentPolicy, ...],
        log: AuditLog,
    ) -> None:
        if not isinstance(service, MovableFileService):
            raise TypeError("service must be a MovableFileService")
        if not isinstance(policies, tuple):
            raise TypeError("policies must be a tuple")
        if not isinstance(log, AuditLog):
            raise TypeError("log must be an AuditLog")
        self._service = service
        self._policies = policies
        self._log = log
        self._failures = 0
        self._lock = threading.Lock()

    @property
    def audit_failures(self) -> int:
        with self._lock:
            return self._failures

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)

    # -- audited operations -------------------------------------------------

    def save(
        self,
        token: str,
        source_id: str,
        file_id: str,
        data: bytes,
        *,
        overwrite: bool = False,
        create_parents: bool = True,
    ) -> dict[str, Any]:
        try:
            result = self._service.save(
                token,
                source_id,
                file_id,
                data,
                overwrite=overwrite,
                create_parents=create_parents,
            )
        except ServiceError as exc:
            self._record(
                op="save",
                token=token,
                source_id=source_id,
                file_id=file_id,
                outcome=exc.code,
                bytes=len(data) if isinstance(data, (bytes, bytearray)) else None,
            )
            raise
        self._record(
            op="save",
            token=token,
            source_id=source_id,
            file_id=file_id,
            outcome="ok",
            bytes=len(data) if isinstance(data, (bytes, bytearray)) else None,
        )
        return result

    def move(
        self,
        token: str,
        source_id: str,
        file_id: str,
        dest_parent_id: str,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        try:
            result = self._service.move(
                token, source_id, file_id, dest_parent_id, new_name
            )
        except ServiceError as exc:
            self._record(
                op="move",
                token=token,
                source_id=source_id,
                file_id=file_id,
                outcome=exc.code,
                dest_parent_id=dest_parent_id,
                new_name=new_name,
            )
            raise
        self._record(
            op="move",
            token=token,
            source_id=source_id,
            file_id=file_id,
            outcome="ok",
            dest_parent_id=dest_parent_id,
            new_name=new_name,
        )
        return result

    def trash(self, token: str, source_id: str, file_id: str) -> dict[str, Any]:
        try:
            result = self._service.trash(token, source_id, file_id)
        except ServiceError as exc:
            self._record(
                op="trash",
                token=token,
                source_id=source_id,
                file_id=file_id,
                outcome=exc.code,
            )
            raise
        self._record(
            op="trash",
            token=token,
            source_id=source_id,
            file_id=file_id,
            outcome="ok",
        )
        return result

    # -- internals ----------------------------------------------------------

    def _agent_for(self, token: Any) -> str:
        if not isinstance(token, str):
            return "unknown"
        try:
            digest = hashlib.sha256(token.encode("ascii")).digest()
        except (UnicodeEncodeError, AttributeError):
            return "unknown"
        match = "unknown"
        for policy in self._policies:
            if not isinstance(policy, AgentPolicy):
                continue
            if hmac.compare_digest(digest, policy.token_sha256):
                match = policy.agent_id
        return match

    def _record(self, *, op: str, token: Any, **fields: Any) -> None:
        record: dict[str, Any] = {
            "op": op,
            "agent": self._agent_for(token),
        }
        for key in ("source_id", "file_id", "dest_parent_id", "new_name", "outcome"):
            if key in fields:
                record[key] = fields.pop(key)
        if "bytes" in fields and fields["bytes"] is not None:
            record["bytes"] = fields.pop("bytes")
        record.update(fields)
        try:
            self._log.record(**record)
        except Exception:
            with self._lock:
                self._failures += 1
