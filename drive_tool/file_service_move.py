"""Move-side service layer: grant-isolated move and trash (MODULE-006).

Adds :class:`MovableFileService`, a thin subclass of
:class:`drive_tool.file_service.LocalFileService` that exposes the two
destructive-but-safe operations implemented by
:class:`drive_tool.local_move.LocalMover`: a no-overwrite rename and a hidden
trash move. Nothing is ever permanently deleted or overwritten.

Both methods mirror :meth:`LocalFileService.save` exactly: authenticate and
authorize first, validate arguments, take a binding lease, translate storage
errors through ``_translate_storage_exception`` with a write in flight (so
failures are reported outcome-uncertain and not retry-safe), and always
release the lease in ``finally``.
"""

from __future__ import annotations

from typing import Any

from drive_tool.file_service import (
    SERVICE_MAX_DEPTH,
    LocalFileService,
    ServiceError,
    _canonical_id,
    _parse_canonical_parts,
    _raise_service_error,
    _require_nonbool_int,
    _translate_storage_exception,
)
from drive_tool.local import (
    LocalStorageError,
    UnsafePathError,
)
from drive_tool.local_move import LocalMover
from drive_tool.service_policy import PolicyDeniedError
from drive_tool.storage import (
    ROOT_ID,
    FileRef,
    StorageError,
)

__all__ = ["MovableFileService"]

def _require_optional_name(value: Any) -> str | None:
    """Validate ``new_name``: ``None`` or a ``str`` (LocalMover re-validates)."""
    if value is None:
        return None
    if not isinstance(value, str):
        _raise_service_error("invalid")
    return value

def _moved_ref(source_id: str, file_id: str) -> dict[str, Any]:
    return {"ref": {"source_id": source_id, "file_id": file_id}}

class MovableFileService(LocalFileService):
    """Local file service extended with safe move and trash operations."""

    def move(
        self,
        token: str,
        source_id: str,
        file_id: str,
        dest_parent_id: str,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        """Move ``file_id`` under ``dest_parent_id``, never overwriting.

        ``new_name`` defaults to the source's own leaf name. Only regular
        files can be moved; directories raise ``invalid``.
        """
        agent, grant = self._authenticate_and_authorize(token, source_id, "move")
        source_parts = _parse_canonical_parts(file_id, allow_root=False)
        source_canonical = _canonical_id(source_parts)
        dest_parts = _parse_canonical_parts(dest_parent_id, allow_root=True)
        dest_canonical = _canonical_id(dest_parts)

        if len(source_parts) - 1 > SERVICE_MAX_DEPTH:
            _raise_service_error("invalid")
        if len(dest_parts) > SERVICE_MAX_DEPTH:
            _raise_service_error("invalid")

        checked_name = _require_optional_name(new_name)

        source_ref = FileRef(grant.alias, source_canonical)
        dest_ref = FileRef(grant.alias, dest_canonical)
        binding = self._acquire_binding_lease(agent.agent_id, grant.alias)
        try:
            mover = LocalMover(binding.adapter)
            meta = mover.move(source_ref, dest_ref, checked_name)
            return _moved_ref(grant.alias, meta.ref.file_id)
        except ServiceError:
            raise
        except (
            PolicyDeniedError,
            LocalStorageError,
            UnsafePathError,
            StorageError,
            OSError,
            ValueError,
        ) as exc:
            raise _translate_storage_exception(exc, is_write_in_flight=True) from None
        finally:
            self._release_binding_lease()

    def trash(
        self,
        token: str,
        source_id: str,
        file_id: str,
    ) -> dict[str, Any]:
        """Move ``file_id`` into the hidden trash directory under the root.

        The response identifies the *original* file only; the trash location
        is never disclosed. Nothing is permanently deleted.
        """
        agent, grant = self._authenticate_and_authorize(token, source_id, "trash")
        parts = _parse_canonical_parts(file_id, allow_root=False)
        canonical_id = _canonical_id(parts)

        if len(parts) - 1 > SERVICE_MAX_DEPTH:
            _raise_service_error("invalid")

        source_ref = FileRef(grant.alias, canonical_id)
        binding = self._acquire_binding_lease(agent.agent_id, grant.alias)
        try:
            mover = LocalMover(binding.adapter)
            mover.trash(source_ref)
            result = _moved_ref(grant.alias, canonical_id)
            result["trashed"] = True
            return result
        except ServiceError:
            raise
        except (
            PolicyDeniedError,
            LocalStorageError,
            UnsafePathError,
            StorageError,
            OSError,
            ValueError,
        ) as exc:
            raise _translate_storage_exception(exc, is_write_in_flight=True) from None
        finally:
            self._release_binding_lease()
