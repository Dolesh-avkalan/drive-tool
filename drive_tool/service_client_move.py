"""Move-side loopback HTTP client for the Drive service (MODULE-006).

Extends :class:`drive_tool.service_client.DriveClient` with the two move-side
JSON endpoints wired in :mod:`drive_tool.http_move`::

    POST /v1/move
    POST /v1/trash

Both are writes: calls are dispatched through ``_dispatch_json(...,
write_op=True)`` exactly like :meth:`DriveClient.save`, so transport or
protocol failures surface as :class:`DriveClientError` with
``outcome_uncertain=True`` and ``retry_safe=False``. The client never retries.

Validation happens locally first, using the base module's own ``_validate_*``
helpers. Lexical checks are a convenience only; the server adapter owns
containment and authorization. The module holds no filesystem access, no
policy logic, no persistent state and no connection reuse.
"""

from __future__ import annotations

from typing import Optional

from drive_tool.service_client import (
    DriveClient,
    _validate_file_id,
    _validate_source_id,
)

__all__ = ["MoveClient"]

def _validate_new_name(new_name: Optional[str]) -> None:
    """``new_name`` is optional; when present it must be a non-empty ``str``."""

    if new_name is None:
        return
    if not isinstance(new_name, str):
        raise TypeError("new_name must be a str")
    if new_name == "":
        raise ValueError("new_name must be non-empty")

class MoveClient(DriveClient):
    """Loopback client for the move-side Drive service operations.

    Constructor arguments are identical to :class:`DriveClient` (``port`` and
    ``token`` required keyword arguments, ``timeout`` an inactivity socket
    budget) and are validated before any network activity.
    """

    # -- public operations --------------------------------------------------

    def move(
        self,
        source_id: str,
        file_id: str,
        dest_parent_id: str,
        new_name: Optional[str] = None,
    ) -> dict:
        """POST /v1/move with JSON controls; return the result object.

        ``file_id`` must be a non-root canonical FileRef id; ``dest_parent_id``
        may be ``"root"`` or a canonical FileRef id. ``new_name`` is sent only
        when it is not ``None``.
        """

        _validate_source_id(source_id)
        _validate_file_id(file_id, allow_root=False)
        _validate_file_id(dest_parent_id, allow_root=True)
        _validate_new_name(new_name)

        payload = {
            "source_id": source_id,
            "file_id": file_id,
            "dest_parent_id": dest_parent_id,
        }
        if new_name is not None:
            payload["new_name"] = new_name
        return self._dispatch_json("/v1/move", payload, write_op=True)

    def trash(self, source_id: str, file_id: str) -> dict:
        """POST /v1/trash with JSON controls; return the result object.

        ``file_id`` must be a non-root canonical FileRef id.
        """

        _validate_source_id(source_id)
        _validate_file_id(file_id, allow_root=False)

        payload = {
            "source_id": source_id,
            "file_id": file_id,
        }
        return self._dispatch_json("/v1/trash", payload, write_op=True)
