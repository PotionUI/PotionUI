"""Mesh thumbnail render-and-store: the one place a mesh preview PNG is
rendered and persisted, so the media-index mesh pass (`indexer.py`) and the
generation-completion trigger (`src.features.generation.orchestrator`) write
through the same `thumbnails/<stem>_medium.png` convention instead of two
copies of it drifting apart.
"""

import logging
import os
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.platform.filesystem import FileStore
    from src.features.media_index.repository import MediaIndexRepository

logger = logging.getLogger(__name__)


def render_and_store_mesh_thumbnail(
    file_service: "FileStore",
    repository: "MediaIndexRepository",
    file_id: str,
    file_path: str,
) -> Optional[str]:
    """Render `file_path`'s mesh to a PNG and persist it as the file's
    thumbnail via `repository.set_thumbnails`.

    Returns the thumbnail path, relative to the file's own directory (the
    same convention `thumbnail_small/medium/large` already use), or None if
    rendering failed - every failure is logged here and never raised, so a
    bad mesh can't take down a caller.
    """
    # Deferred: `mesh_preview` imports torch, and both callers of this
    # function are reachable from the boot import chain (`test_bootstrap_
    # app_import_leaves_heavy_modules_unimported`) - import it here, at the
    # one call site that actually renders a mesh, not at module load.
    from src.platform.runtime.native.mesh_preview import MeshPreviewError, render_mesh_preview

    mesh_path = file_service.get_full_path(file_path)
    try:
        png_bytes = render_mesh_preview(mesh_path)
    except MeshPreviewError:
        logger.warning(
            "mesh file %s does not parse as a renderable glTF-binary, skipping", file_id
        )
        return None
    except FileNotFoundError:
        logger.warning("mesh file %s is gone (%s), skipping", file_id, mesh_path)
        return None
    except Exception:
        logger.exception("mesh preview render failed for file %s", file_id)
        return None

    base_key = os.path.dirname(file_path)
    stem = os.path.splitext(os.path.basename(file_path))[0]
    relative = f"thumbnails/{stem}_medium.png"
    key = f"{base_key}/{relative}" if base_key else relative
    file_service.storage_driver.put_bytes(key, png_bytes)
    repository.set_thumbnails(file_id, relative, relative, relative)
    return relative
