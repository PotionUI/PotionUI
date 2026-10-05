import logging
import random
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from src.features.forms.binding import form_field_index
from src.features.forms.exceptions import FormNotFoundException
from src.features.generation.dto import GenerationRequest
from src.features.generation.failure import scope_generation
from src.features.generation.grids.dto import (
    DEFAULT_CONFIRM_ABOVE,
    HARD_CAP,
    CreateGridRequest,
    GridCellRef,
)
from src.features.generation.grids.expansion import (
    Cell,
    cell_key,
    display_value,
    expand_cells,
    grid_shape,
    positions,
    queue_order,
)
from src.features.generation.grids.repository import GridRecord, GridRepository
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from src.platform.plugins.limit_kinds import AdmissionRequest

logger = logging.getLogger(__name__)

ACTIVE_STATES = frozenset({"pending", "running"})
SETTING_CONFIRM_ABOVE = "compare_confirm_above"
CELL_STATUS = {"pending": "queued"}


class GridError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422, extra: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.extra = extra or {}


SubmitCell = Callable[[GenerationRequest, Any, GridCellRef], Awaitable[Dict[str, Any]]]


class GridService:
    def __init__(
        self,
        grids: GridRepository,
        generations: GenerationRepository,
        history,
        preset_loader,
        settings,
        submit: SubmitCell,
        cancel: Callable[[str], Awaitable[Any]],
        limit_guard=None,
        is_admin: Callable[[Any], bool] = lambda user: False,
        rng: Optional[random.Random] = None,
    ):
        self.grids = grids
        self.generations = generations
        self.history = history
        self.preset_loader = preset_loader
        self.settings = settings
        self.submit = submit
        self.cancel = cancel
        self.limit_guard = limit_guard
        self.is_admin = is_admin
        self.rng = rng or random.Random()

    def read_settings(self) -> Dict[str, int]:
        raw = self.settings.get_setting(SETTING_CONFIRM_ABOVE, DEFAULT_CONFIRM_ABOVE)
        try:
            confirm_above = int(raw)
        except (TypeError, ValueError):
            confirm_above = DEFAULT_CONFIRM_ABOVE
        return {"confirm_above": max(0, confirm_above), "hard_cap": HARD_CAP}

    def _template(self, preset_id: str):
        template = self.preset_loader.load_preset_by_id(preset_id)
        if not template:
            raise GridError("preset_not_found", f"Preset '{preset_id}' not found", 404)
        return template

    def _field_index(self, template, mode: str, form_name: Optional[str]):
        try:
            return form_field_index(template, mode, form_name)
        except FormNotFoundException as exc:
            raise GridError("form_not_found", str(exc), 404)

    def _validate(self, body: CreateGridRequest) -> None:
        if body.y_axis is not None and body.y_axis.field == body.x_axis.field:
            raise GridError("axis_conflict", "The same field cannot be on both axes")
        cols, rows = grid_shape(body.x_axis, body.y_axis)
        if cols * rows > HARD_CAP:
            raise GridError(
                "grid_too_large",
                f"A grid can have at most {HARD_CAP} cells, this one has {cols * rows}",
                extra={"cells": cols * rows, "hard_cap": HARD_CAP},
            )

    def _admit(self, user, template, preset_id: str, count: int) -> None:
        if self.limit_guard is None:
            return
        self.limit_guard.check_batch(
            AdmissionRequest(
                point="submit",
                user_id=user.id,
                engine=getattr(template, "engine", None),
                preset_id=preset_id,
            ),
            count,
        )

    async def create(self, user, body: CreateGridRequest) -> Dict[str, Any]:
        self._validate(body)
        request = body.request
        mode = request.mode or "txt2img"
        template = self._template(request.preset_id)
        field_index = self._field_index(template, mode, request.form_name)
        cols, rows = grid_shape(body.x_axis, body.y_axis)
        self._admit(user, template, request.preset_id, cols * rows)
        cells = expand_cells(
            request,
            body.x_axis,
            body.y_axis,
            body.lock_seed,
            field_index,
            preset_id=request.preset_id,
            mode=mode,
            rng=self.rng,
        )
        seeds = {cell_key(cell.x, cell.y): cell.seed for cell in cells if cell.seed is not None}
        grid = self.grids.create(
            user.id,
            request.preset_id,
            request.tab_id,
            body.x_axis,
            body.y_axis,
            body.lock_seed,
            request.model_dump(),
            seeds,
        )
        try:
            await self._submit_all(user, grid, queue_order(cells))
        except BaseException:
            await self._discard(grid, user)
            raise
        return await self.serialize(grid, user)

    async def _submit_all(self, user, grid: GridRecord, ordered: List[Cell]) -> List[str]:
        created: List[str] = []
        for cell in ordered:
            ref = GridCellRef(grid.id, cell.x, cell.y, cell.axis_values)
            result = await self.submit(cell.request, user, ref)
            created.append(result["generation_id"])
        return created

    async def _discard(self, grid: GridRecord, user) -> None:
        try:
            await self._remove(grid, user)
        except Exception:
            logger.exception("Could not roll back grid %s", grid.id)

    async def _remove(self, grid: GridRecord, user) -> None:
        cells = self.grids.cells(grid.id)
        for cell in cells:
            if cell.status in ACTIVE_STATES:
                try:
                    await self.cancel(cell.id)
                except Exception:
                    logger.exception("Could not cancel generation %s while removing grid %s", cell.id, grid.id)
        ids = [cell.id for cell in cells]
        if ids:
            if user.id == grid.user_id:
                self.history.bulk_delete(ids, grid.user_id)
            else:
                self.history.admin_bulk_delete(ids, user.id)
        self.grids.delete(grid.id)

    def load(self, grid_id: str, user) -> GridRecord:
        grid = self.grids.get(grid_id)
        if grid is None or (grid.user_id != user.id and not self.is_admin(user)):
            raise GridError("grid_not_found", "Grid not found", 404)
        return grid

    async def get(self, grid_id: str, user) -> Dict[str, Any]:
        return await self.serialize(self.load(grid_id, user), user)

    async def delete(self, grid_id: str, user) -> None:
        await self._remove(self.load(grid_id, user), user)

    async def retry_failed(self, grid_id: str, user) -> Dict[str, Any]:
        grid = self.load(grid_id, user)
        latest = self._latest_by_position(self.grids.cells(grid.id))
        wanted: List[Tuple[int, int]] = []
        stale: Dict[Tuple[int, int], List[str]] = {}
        for position in positions(grid.x_axis, grid.y_axis):
            existing = latest.get(position)
            if existing is None or existing.status == "failed":
                wanted.append(position)
                if existing is not None:
                    stale[position] = [existing.id]
        if not wanted:
            return await self.serialize(grid, user)
        base = GenerationRequest(**grid.base_request)
        mode = base.mode or "txt2img"
        template = self._template(grid.preset_id)
        field_index = self._field_index(template, mode, base.form_name)
        self._admit(user, template, grid.preset_id, len(wanted))
        cells = expand_cells(
            base,
            grid.x_axis,
            grid.y_axis,
            grid.lock_seed,
            field_index,
            preset_id=grid.preset_id,
            mode=mode,
            rng=self.rng,
            stored_seeds=grid.seeds,
            only=wanted,
        )
        for cell in queue_order(cells):
            ref = GridCellRef(grid.id, cell.x, cell.y, cell.axis_values)
            retry_key = f"retry-{self.rng.getrandbits(32):08x}"
            if cell.request.idempotency_key:
                cell.request.idempotency_key = f"{cell.request.idempotency_key[:150]}:{retry_key}"
            await self.submit(cell.request, user, ref)
            old = stale.get((cell.x, cell.y))
            if old:
                self._drop_failed(old, grid)
        return await self.serialize(grid, user)

    def _drop_failed(self, generation_ids: List[str], grid: GridRecord) -> None:
        try:
            self.history.bulk_delete(generation_ids, grid.user_id)
        except Exception:
            logger.exception("Could not remove replaced failed cells of grid %s", grid.id)

    @staticmethod
    def _latest_by_position(rows: List[Generation]) -> Dict[Tuple[int, int], Generation]:
        latest: Dict[Tuple[int, int], Generation] = {}
        for row in rows:
            if row.grid_x is None or row.grid_y is None:
                continue
            key = (row.grid_x, row.grid_y)
            current = latest.get(key)
            if current is None or row.id > current.id:
                latest[key] = row
        return latest

    @staticmethod
    def derive_status(statuses: List[str]) -> str:
        if any(status in ("queued", "running") for status in statuses):
            return "running"
        if statuses and all(status == "completed" for status in statuses):
            return "completed"
        if statuses and all(status in ("cancelled", "deleted") for status in statuses) and "cancelled" in statuses:
            return "cancelled"
        return "partial"

    def _thumbnail(self, item: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        files = [f for f in item.get("files") or [] if f.get("id")]
        if not files:
            return None, None
        chosen = next((f for f in files if f.get("is_final")), files[0])
        media = str(chosen.get("file_type") or "").lower() or None
        return f"/api/media/files/{chosen['id']}?size=medium", media

    async def serialize(self, grid: GridRecord, user) -> Dict[str, Any]:
        rows = self.grids.cells(grid.id)
        latest = self._latest_by_position(rows)
        admin = self.is_admin(user)
        dicts = self.history.query.serialize_generations(list(latest.values()), False, viewer_id=user.id)
        by_id = {item["id"]: item for item in dicts}
        cells: List[Dict[str, Any]] = []
        for x, y in positions(grid.x_axis, grid.y_axis):
            row = latest.get((x, y))
            expected = self._expected_values(grid, x, y)
            if row is None:
                cells.append({
                    "x": x, "y": y, "generation_id": None, "status": "deleted",
                    "axis_values": expected, "seed": grid.seeds.get(cell_key(x, y)),
                    "thumbnail_url": None, "media_type": None, "error": None,
                })
                continue
            item = by_id.get(row.id)
            thumb, media = self._thumbnail(item) if item else (None, None)
            error = None
            if row.status == "failed":
                scoped = scope_generation(row.to_dict(), admin)
                error = scoped.get("error_message")
            seed = row.form_data.get("seed") if isinstance(row.form_data, dict) else None
            cells.append({
                "x": x,
                "y": y,
                "generation_id": row.id,
                "status": CELL_STATUS.get(row.status, row.status),
                "axis_values": row.axis_values or expected,
                "seed": seed if isinstance(seed, int) and seed >= 0 else grid.seeds.get(cell_key(x, y)),
                "thumbnail_url": thumb,
                "media_type": media,
                "error": error,
            })
        return {
            "id": grid.id,
            "preset_id": grid.preset_id,
            "tab_id": grid.tab_id,
            "x_axis": grid.x_axis.model_dump(),
            "y_axis": grid.y_axis.model_dump() if grid.y_axis is not None else None,
            "lock_seed": grid.lock_seed,
            "status": self.derive_status([cell["status"] for cell in cells]),
            "created_at": grid.created_iso(),
            "cells": cells,
        }

    @staticmethod
    def _expected_values(grid: GridRecord, x: int, y: int) -> Dict[str, Any]:
        values = {grid.x_axis.field: display_value(grid.x_axis.values[x])}
        if grid.y_axis is not None:
            values[grid.y_axis.field] = display_value(grid.y_axis.values[y])
        return values
