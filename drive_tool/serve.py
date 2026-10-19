"""Launcher for the drive-tool loopback HTTP service (standard library only).

Reads a strict JSON config file, builds the frozen policy objects from
``service_policy``, starts a :class:`MoveHTTPAdapter` on loopback, and waits
for a stop event. No filesystem access beyond config stat/read and the
``os.path.isdir`` root checks; no network beyond the adapter itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import threading

from drive_tool.audit import AuditLog, AuditedService
from drive_tool.file_service_move import MovableFileService
from drive_tool.http_move import MoveHTTPAdapter
from drive_tool.service_policy import AgentPolicy, SourceGrant

__all__ = [
    "ConfigError",
    "load_config",
    "load_audit_log",
    "hash_token",
    "run",
    "main",
]

_MAX_CONFIG_BYTES = 1024 * 1024
_OPERATIONS = frozenset({"list", "find", "read", "save", "move", "trash"})
_HEX_DIGITS = frozenset("0123456789abcdef")

_TOP_KEYS = frozenset({"port", "agents"})
_TOP_OPTIONAL_KEYS = frozenset({"audit_log"})
_AGENT_KEYS = frozenset({"agent_id", "token_sha256", "grants"})
_GRANT_REQUIRED_KEYS = frozenset({"alias", "root", "operations"})
_GRANT_OPTIONAL_KEYS = frozenset({"max_read_bytes", "max_write_bytes"})
_GRANT_KEYS = _GRANT_REQUIRED_KEYS | _GRANT_OPTIONAL_KEYS

class ConfigError(Exception):
    """Raised for any config load / validation failure."""

    __slots__ = ()

def _no_dup_keys(pairs):
    seen = set()
    out = {}
    for key, value in pairs:
        if key in seen:
            raise ConfigError("duplicate JSON key")
        seen.add(key)
        out[key] = value
    return out

def _no_nan_inf(value):
    raise ConfigError("non-finite JSON constant")

def _load_json(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        raise ConfigError("cannot stat config file") from None
    if size > _MAX_CONFIG_BYTES:
        raise ConfigError("config file too large")
    try:
        mode = os.stat(path).st_mode
    except OSError:
        raise ConfigError("cannot stat config file") from None
    if mode & 0o022:
        raise ConfigError("config file must not be group/other writable")
    try:
        with open(path, "rb") as fh:
            raw = fh.read(_MAX_CONFIG_BYTES + 1)
    except OSError:
        raise ConfigError("cannot read config file") from None
    if len(raw) > _MAX_CONFIG_BYTES:
        raise ConfigError("config file too large")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ConfigError("config file must be valid UTF-8") from None
    try:
        return json.loads(
            text, object_pairs_hook=_no_dup_keys, parse_constant=_no_nan_inf
        )
    except ConfigError:
        raise
    except ValueError:
        raise ConfigError("config file is not valid JSON") from None

def _require_mapping(value, what):
    if not isinstance(value, dict):
        raise ConfigError("%s must be an object" % (what,))
    return value

def _require_keys(obj, required, allowed, what):
    keys = set(obj.keys())
    if not required.issubset(keys):
        raise ConfigError("%s is missing required keys" % (what,))
    if not keys.issubset(allowed):
        raise ConfigError("%s has unknown keys" % (what,))

def _parse_port(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("port must be an int")
    if value < 0 or value > 65535:
        raise ConfigError("port must be in 0..65535")
    return value

def _parse_token_sha256(value):
    if not isinstance(value, str):
        raise ConfigError("token_sha256 must be a string")
    if len(value) != 64:
        raise ConfigError("token_sha256 must be 64 lowercase hex characters")
    for ch in value:
        if ch not in _HEX_DIGITS:
            raise ConfigError("token_sha256 must be 64 lowercase hex characters")
    return bytes.fromhex(value)

def _parse_operations(value):
    if not isinstance(value, list):
        raise ConfigError("operations must be an array")
    if not value:
        raise ConfigError("operations must be non-empty")
    for op in value:
        if not isinstance(op, str):
            raise ConfigError("operations members must be strings")
        if op not in _OPERATIONS:
            raise ConfigError("operations contains an unsupported operation")
    return frozenset(value)

def _parse_cap(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("%s must be an int" % (name,))
    return value

def _parse_grant(value, index):
    what = "agents[%d].grants[%d]" % (index[0], index[1])
    _require_mapping(value, what)
    _require_keys(value, _GRANT_REQUIRED_KEYS, _GRANT_KEYS, what)

    alias = value["alias"]
    root = value["root"]
    operations = _parse_operations(value["operations"])
    max_read_bytes = _parse_cap(value.get("max_read_bytes"), "max_read_bytes")
    max_write_bytes = _parse_cap(value.get("max_write_bytes"), "max_write_bytes")

    if not isinstance(alias, str):
        raise ConfigError("%s.alias must be a string" % (what,))
    if not isinstance(root, str):
        raise ConfigError("%s.root must be a string" % (what,))

    kwargs = {}
    if max_read_bytes is not None:
        kwargs["max_read_bytes"] = max_read_bytes
    if max_write_bytes is not None:
        kwargs["max_write_bytes"] = max_write_bytes

    try:
        grant = SourceGrant(
            alias=alias, root=root, operations=operations, **kwargs
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError("%s is invalid: %s" % (what, exc)) from None

    if not os.path.isdir(grant.root):
        raise ConfigError("%s.root must be an existing directory" % (what,))
    return grant

def _parse_agent(value, index):
    what = "agents[%d]" % (index,)
    _require_mapping(value, what)
    _require_keys(value, _AGENT_KEYS, _AGENT_KEYS, what)

    agent_id = value["agent_id"]
    token_sha256 = _parse_token_sha256(value["token_sha256"])
    grants_raw = value["grants"]

    if not isinstance(agent_id, str):
        raise ConfigError("%s.agent_id must be a string" % (what,))
    if not isinstance(grants_raw, list):
        raise ConfigError("%s.grants must be an array" % (what,))
    if not grants_raw:
        raise ConfigError("%s.grants must be non-empty" % (what,))

    grants = tuple(
        _parse_grant(grant, (index, i)) for i, grant in enumerate(grants_raw)
    )

    try:
        return AgentPolicy(
            agent_id=agent_id, token_sha256=token_sha256, grants=grants
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError("%s is invalid: %s" % (what, exc)) from None

def _validate_audit_log_path(value, agents):
    """Validate ``audit_log`` value against grant roots; return it unchanged."""
    if not isinstance(value, str):
        raise ConfigError("audit_log must be a string")
    if not value:
        raise ConfigError("audit_log must be a nonempty path")
    if not os.path.isabs(value):
        raise ConfigError("audit_log must be an absolute path")
    parent = os.path.dirname(value)
    if not parent or not os.path.isdir(parent):
        raise ConfigError("audit_log parent directory must exist")
    audit_real = os.path.realpath(value)
    for agent in agents:
        for grant in agent.grants:
            root_real = os.path.realpath(grant.root)
            if os.path.commonpath([audit_real, root_real]) == root_real:
                raise ConfigError(
                    "audit_log must not be inside a grant root"
                )
    return value

def _load_parsed(path):
    data = _load_json(path)
    _require_mapping(data, "config")
    _require_keys(
        data, _TOP_KEYS, _TOP_KEYS | _TOP_OPTIONAL_KEYS, "config"
    )
    return data

def load_config(path):
    """Load and validate the config file, returning ``(port, agents)``."""

    data = _load_parsed(path)

    port = _parse_port(data["port"])
    agents_raw = data["agents"]
    if not isinstance(agents_raw, list):
        raise ConfigError("agents must be an array")
    if not agents_raw:
        raise ConfigError("agents must be non-empty")

    agents = tuple(
        _parse_agent(agent, i) for i, agent in enumerate(agents_raw)
    )

    seen_ids = set()
    seen_digests = set()
    for agent in agents:
        if agent.agent_id in seen_ids:
            raise ConfigError("duplicate agent_id")
        seen_ids.add(agent.agent_id)
        if agent.token_sha256 in seen_digests:
            raise ConfigError("duplicate token_sha256")
        seen_digests.add(agent.token_sha256)

    if "audit_log" in data:
        _validate_audit_log_path(data["audit_log"], agents)

    return port, agents

def load_audit_log(path):
    """Return the validated ``audit_log`` path, or ``None`` when absent."""

    data = _load_parsed(path)
    if "audit_log" not in data:
        return None
    _, agents = load_config(path)
    return _validate_audit_log_path(data["audit_log"], agents)

def hash_token(token):
    """Return the lowercase hex SHA-256 of a UTF-8 encoded token."""

    if not isinstance(token, str):
        raise TypeError("token must be a str")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

class _AuditedMoveAdapter(MoveHTTPAdapter):
    """MoveHTTPAdapter whose service is the audited wrapper from construction.

    The base constructor validates ``service`` as a ``MovableFileService`` and
    starts the accept thread, so the audited wrapper cannot be installed after
    the fact without a race window. This subclass passes the raw service to the
    base constructor (satisfying its type check) and redirects the inherited
    ``self._service`` lookups to the wrapper via a property.
    """

    __slots__ = ("_audited",)

    def __init__(self, service, audited, port):
        self._audited = audited
        super().__init__(service=service, port=port)

    @property
    def _service(self):
        return self._audited

    @_service.setter
    def _service(self, value):
        pass

def _build_adapter(agents, audit_path, port):
    """Construct the adapter, wiring in AuditedService when configured."""
    service = MovableFileService(agents)
    if audit_path is None:
        return MoveHTTPAdapter(service=service, port=port)
    audited = AuditedService(service, agents, AuditLog(audit_path))
    return _AuditedMoveAdapter(service, audited, port)

def run(config_path, *, ready=None, stop=None):
    """Load config, start the adapter, wait for ``stop``, then close."""

    port, agents = load_config(config_path)
    audit_path = load_audit_log(config_path)
    adapter = _build_adapter(agents, audit_path, port)
    try:
        if ready is not None:
            ready(adapter.port)
        if stop is not None:
            stop.wait()
        closed = adapter.close(timeout=5.0)
        return 0 if closed else 1
    finally:
        try:
            adapter.close(timeout=5.0)
        except Exception:
            pass

def _do_hash_token():
    line = sys.stdin.readline()
    if line == "":
        line = ""
    if line.endswith("\n"):
        line = line[:-1]
    if line.endswith("\r"):
        line = line[:-1]
    sys.stdout.write(hash_token(line) + "\n")
    sys.stdout.flush()
    return 0

def main(argv=None):
    parser = argparse.ArgumentParser(prog="drive_tool.serve", add_help=True)
    parser.add_argument("--config", dest="config", default=None)
    parser.add_argument("--hash-token", dest="hash_token", action="store_true")
    args = parser.parse_args(argv)

    if args.hash_token:
        return _do_hash_token()

    if args.config is None:
        sys.stderr.write("config error: --config is required\n")
        return 2

    try:
        port, agents = load_config(args.config)
        audit_path = load_audit_log(args.config)
    except ConfigError as exc:
        sys.stderr.write("config error: %s\n" % (exc,))
        return 2

    stop = threading.Event()

    def _handle_signal(signum, frame):
        stop.set()

    old_term = signal.signal(signal.SIGTERM, _handle_signal)
    old_int = signal.signal(signal.SIGINT, _handle_signal)
    try:
        adapter = _build_adapter(agents, audit_path, port)
        try:
            sys.stderr.write(
                "drive-tool listening on 127.0.0.1:%d (%d agents)\n"
                % (adapter.port, len(agents))
            )
            sys.stderr.flush()
            stop.wait()
            closed = adapter.close(timeout=5.0)
            return 0 if closed else 1
        finally:
            try:
                adapter.close(timeout=5.0)
            except Exception:
                pass
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)

if __name__ == "__main__":
    raise SystemExit(main())
