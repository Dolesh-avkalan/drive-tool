"""Move-side HTTP adapter: POST /v1/move and POST /v1/trash (MODULE-006).

Extends :class:`drive_tool.http_adapter.DriveHTTPAdapter` with the two
move-side JSON endpoints, wired to a
:class:`drive_tool.file_service_move.MovableFileService`. Everything else —
bounded loopback listener, fixed worker topology, monotonic deadlines,
auth-first delegation, redacted error envelopes — is inherited unchanged.

Both endpoints follow the same shape as ``list`` / ``find`` / ``read``: POST,
strict UTF-8 JSON body, exact key rules, no trailing bytes. Payloads that fail
validation are still delegated (with ``file_id=None``) so authentication and
authorization always run before any filesystem access. Unexpected failures
answer ``503 unavailable`` with ``outcome_uncertain=True`` and
``retry_safe=False`` — these are writes, mirroring ``save``.
"""

from __future__ import annotations

from drive_tool.file_service import ServiceError
from drive_tool.file_service_move import MovableFileService
from drive_tool.http_adapter import (
    _JSON_KEY_RULES,
    DriveHTTPAdapter,
    _parse_json_strict,
    _ReqErrorSignal,
    _SERVICE_ERROR_STATUS,
)

__all__ = ["MoveHTTPAdapter"]

# (required keys, optional keys) per move-side JSON endpoint.
_MOVE_JSON_KEY_RULES = {
    "move": (
        frozenset({"source_id", "file_id", "dest_parent_id"}),
        frozenset({"new_name"}),
    ),
    "trash": (
        frozenset({"source_id", "file_id"}),
        frozenset(),
    ),
}

# Endpoints whose dispatcher failure mode is a write (outcome uncertain).
_MOVE_WRITE_ENDPOINTS = frozenset({"move", "trash"})

def _register_key_rules():
    """Install move-side key rules in the base module's rule table.

    The base class consults ``_JSON_KEY_RULES`` (the same dict object) in
    ``_validate_json_payload``, so updating it in place lets the inherited
    validation logic see the new endpoints without touching the base module.
    """
    _JSON_KEY_RULES.update(_MOVE_JSON_KEY_RULES)

_register_key_rules()

class MoveHTTPAdapter(DriveHTTPAdapter):
    """Loopback HTTP/1.1 adapter exposing move and trash over MovableFileService."""

    # ----- construction ----------------------------------------------------

    def __init__(
        self,
        policies=None,
        *,
        service=None,
        port=0,
        socket_timeout=None,
    ):
        if service is None and policies is not None:
            service = MovableFileService(policies)
            kwargs = {"service": service, "port": port}
            if socket_timeout is not None:
                kwargs["socket_timeout"] = socket_timeout
            super().__init__(**kwargs)
            return
        if service is None:
            raise ValueError("either policies or service must be provided")
        if not isinstance(service, MovableFileService):
            raise TypeError("service must be a MovableFileService instance")
        kwargs = {"service": service, "port": port}
        if socket_timeout is not None:
            kwargs["socket_timeout"] = socket_timeout
        super().__init__(**kwargs)

    # ----- routing ---------------------------------------------------------

    def _parse_target(self, target):
        if target == b"/v1/move":
            return ("move", "")
        if target == b"/v1/trash":
            return ("trash", "")
        return super()._parse_target(target)

    # ----- payload validation ---------------------------------------------

    def _validate_json_payload(self, obj, endpoint):
        rules = _MOVE_JSON_KEY_RULES.get(endpoint)
        if rules is None:
            return super()._validate_json_payload(obj, endpoint)
        required, optional = rules
        keys = set(obj.keys())
        if not required.issubset(keys):
            return False
        if not keys.issubset(required | optional):
            return False
        if endpoint == "move":
            if "new_name" in obj and not isinstance(obj["new_name"], str):
                return False
        return True

    # ----- dispatch --------------------------------------------------------

    def _dispatch(self, conn, request):
        endpoint = request.endpoint
        if endpoint not in _MOVE_WRITE_ENDPOINTS:
            return super()._dispatch(conn, request)

        token = request.token
        try:
            if endpoint == "move":
                result = self._call_move(token, request.body)
            else:  # trash
                result = self._call_trash(token, request.body)
        except ServiceError as exc:
            status = _SERVICE_ERROR_STATUS.get(exc.code, 503)
            self._safe_send_error(
                conn, status, exc.code, exc.outcome_uncertain, exc.retry_safe
            )
            return
        except BaseException:
            self._safe_send_error(conn, 503, "unavailable", True, False)
            return

        self._send_json_response(conn, result, endpoint)

    # ----- auth-first public delegation -----------------------------------

    def _parse_json_dict(self, body):
        try:
            parsed = _parse_json_strict(body)
        except (ValueError, UnicodeError, RecursionError):
            return None
        if isinstance(parsed, dict):
            return parsed
        return None

    def _extract_candidate_source_id(self, obj):
        if isinstance(obj, dict):
            src = obj.get("source_id")
            if isinstance(src, str):
                return src
        return None

    def _call_move(self, token, body):
        obj = self._parse_json_dict(body)
        candidate_source_id = self._extract_candidate_source_id(obj)

        if obj is not None and self._validate_json_payload(obj, "move"):
            return self._service.move(
                token,
                obj["source_id"],
                obj["file_id"],
                obj["dest_parent_id"],
                obj.get("new_name"),
            )
        # Malformed: delegate with file_id=None so auth/authorize run first.
        return self._service.move(token, candidate_source_id, None, None)

    def _call_trash(self, token, body):
        obj = self._parse_json_dict(body)
        candidate_source_id = self._extract_candidate_source_id(obj)

        if obj is not None and self._validate_json_payload(obj, "trash"):
            return self._service.trash(
                token,
                obj["source_id"],
                obj["file_id"],
            )
        return self._service.trash(token, candidate_source_id, None)
