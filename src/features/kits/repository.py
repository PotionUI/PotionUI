import uuid
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from src.features.kits.records import Kit, KitItem
from src.features.kits.rules import ITEM_KINDS, ITEM_ID_PREFIX, KIT_ID_PREFIX, MEDIA_KINDS
from src.features.media.records import Upload
from src.platform.database import get_cursor
from src.platform.database.rows import dt_column, dt_iso, now_iso

THUMBNAILS_PER_KIT = 4


def new_kit_id() -> str:
    return KIT_ID_PREFIX + uuid.uuid4().hex[:8]


def new_item_id() -> str:
    return ITEM_ID_PREFIX + uuid.uuid4().hex[:8]


def _marks(values: Sequence) -> str:
    return ",".join("?" * len(values))


class KitRepository:

    def list_kits(
        self, owner_id: str, label: Optional[str] = None, search: Optional[str] = None,
        limit: int = 50, offset: int = 0,
    ) -> Tuple[List[Kit], int]:
        clauses = ["owner_id = ?"]
        params: list = [owner_id]
        if label:
            clauses.append("LOWER(COALESCE(label, '')) = LOWER(?)")
            params.append(label)
        if search:
            clauses.append("(LOWER(name) LIKE LOWER(?) OR handle LIKE LOWER(?))")
            params.extend([f"%{search}%", f"%{search}%"])
        where = " AND ".join(clauses)
        with get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS count FROM kits WHERE {where}", params)
            total = cursor.fetchone()["count"]
            cursor.execute(
                f"SELECT * FROM kits WHERE {where} ORDER BY position ASC, created_at ASC, id ASC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            )
            return [Kit.from_row(row) for row in cursor.fetchall()], total

    def labels(self, owner_id: str) -> List[dict]:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT label, COUNT(*) AS count FROM kits WHERE owner_id = ? AND label IS NOT NULL AND label != '' "
                "GROUP BY label ORDER BY label",
                (owner_id,),
            )
            return [{"label": row["label"], "count": row["count"]} for row in cursor.fetchall()]

    def cited_in_tabs(self, owner_id: str, kit_id: str, limit: int = 20) -> Tuple[int, int, List[dict]]:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT tab_id, updated_at, instr(doc, '\"video_director\"') > 0 AS director FROM tab_drafts "
                "WHERE user_id = ? AND instr(doc, ?) > 0 ORDER BY updated_at DESC, tab_id",
                (owner_id, kit_id),
            )
            rows = cursor.fetchall()
        tabs = [
            {"tab_id": row["tab_id"], "updated_at": dt_iso(dt_column(row["updated_at"])), "director": bool(row["director"])}
            for row in rows
        ]
        return len(tabs), sum(1 for tab in tabs if tab["director"]), tabs[:limit]

    def get(self, kit_id: str, owner_id: str) -> Optional[Kit]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM kits WHERE id = ? AND owner_id = ?", (kit_id, owner_id))
            row = cursor.fetchone()
            return Kit.from_row(row) if row else None

    def get_many(self, kit_ids: Iterable[str], owner_id: str) -> Dict[str, Kit]:
        ids = list(dict.fromkeys(kit_ids))
        if not ids:
            return {}
        with get_cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM kits WHERE owner_id = ? AND id IN ({_marks(ids)})", (owner_id, *ids)
            )
            return {row["id"]: Kit.from_row(row) for row in cursor.fetchall()}

    def get_by_handle(self, owner_id: str, handle: str) -> Optional[Kit]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM kits WHERE owner_id = ? AND handle = ?", (owner_id, handle))
            row = cursor.fetchone()
            if row:
                return Kit.from_row(row)
            cursor.execute(
                "SELECT k.* FROM kit_handle_aliases a JOIN kits k ON k.id = a.kit_id "
                "WHERE a.owner_id = ? AND a.handle = ?",
                (owner_id, handle),
            )
            row = cursor.fetchone()
            return Kit.from_row(row) if row else None

    def handle_taken(self, owner_id: str, handle: str, except_kit_id: Optional[str] = None) -> bool:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM kits WHERE owner_id = ? AND handle = ? AND id != ?",
                (owner_id, handle, except_kit_id or ""),
            )
            return cursor.fetchone() is not None

    def next_position(self, owner_id: str) -> int:
        with get_cursor() as cursor:
            cursor.execute("SELECT COALESCE(MAX(position), -1) + 1 AS next FROM kits WHERE owner_id = ?", (owner_id,))
            return cursor.fetchone()["next"]

    def create(self, owner_id: str, name: str, handle: str, label: Optional[str]) -> Kit:
        kit_id = new_kit_id()
        now = now_iso()
        position = self.next_position(owner_id)
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM kit_handle_aliases WHERE owner_id = ? AND handle = ?", (owner_id, handle))
            cursor.execute(
                "INSERT INTO kits (id, owner_id, name, handle, label, position, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (kit_id, owner_id, name, handle, label, position, now, now),
            )
        return self.get(kit_id, owner_id)

    def update(self, kit: Kit, previous_handle: Optional[str] = None) -> Kit:
        now = now_iso()
        with get_cursor() as cursor:
            if previous_handle and previous_handle != kit.handle:
                cursor.execute(
                    "DELETE FROM kit_handle_aliases WHERE owner_id = ? AND handle = ?", (kit.owner_id, kit.handle)
                )
                cursor.execute(
                    "INSERT OR REPLACE INTO kit_handle_aliases (owner_id, handle, kit_id) VALUES (?, ?, ?)",
                    (kit.owner_id, previous_handle, kit.id),
                )
            cursor.execute(
                "UPDATE kits SET name = ?, handle = ?, label = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                (kit.name, kit.handle, kit.label, now, kit.id, kit.owner_id),
            )
        return self.get(kit.id, kit.owner_id)

    def touch(self, kit_id: str) -> None:
        with get_cursor() as cursor:
            cursor.execute("UPDATE kits SET updated_at = ? WHERE id = ?", (now_iso(), kit_id))

    def delete(self, kit_id: str, owner_id: str) -> bool:
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM kits WHERE id = ? AND owner_id = ?", (kit_id, owner_id))
            return cursor.rowcount > 0

    def set_order(self, owner_id: str, kit_ids: Sequence[str]) -> None:
        with get_cursor() as cursor:
            for position, kit_id in enumerate(kit_ids):
                cursor.execute(
                    "UPDATE kits SET position = ? WHERE id = ? AND owner_id = ?", (position, kit_id, owner_id)
                )

    def items(self, kit_id: str) -> List[KitItem]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM kit_items WHERE kit_id = ? ORDER BY position ASC, created_at ASC, id ASC", (kit_id,))
            return [KitItem.from_row(row) for row in cursor.fetchall()]

    def items_for_kits(self, kit_ids: Iterable[str]) -> Dict[str, List[KitItem]]:
        ids = list(dict.fromkeys(kit_ids))
        grouped: Dict[str, List[KitItem]] = {kit_id: [] for kit_id in ids}
        if not ids:
            return grouped
        with get_cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM kit_items WHERE kit_id IN ({_marks(ids)}) ORDER BY position ASC, created_at ASC, id ASC",
                ids,
            )
            for row in cursor.fetchall():
                grouped[row["kit_id"]].append(KitItem.from_row(row))
        return grouped

    def get_item(self, kit_id: str, item_id: str) -> Optional[KitItem]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM kit_items WHERE kit_id = ? AND id = ?", (kit_id, item_id))
            row = cursor.fetchone()
            return KitItem.from_row(row) if row else None

    def count_items(self, kit_id: str) -> int:
        with get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS count FROM kit_items WHERE kit_id = ?", (kit_id,))
            return cursor.fetchone()["count"]

    def add_item(
        self, kit_id: str, kind: str, name: str, media_ref: Optional[str] = None, text: Optional[str] = None,
    ) -> KitItem:
        if kind not in ITEM_KINDS:
            raise ValueError(f"unknown kit item kind {kind!r}")
        item_id = new_item_id()
        with get_cursor() as cursor:
            cursor.execute("SELECT COALESCE(MAX(position), -1) + 1 AS next FROM kit_items WHERE kit_id = ?", (kit_id,))
            position = cursor.fetchone()["next"]
            cursor.execute(
                "INSERT INTO kit_items (id, kit_id, kind, name, position, media_ref, text, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (item_id, kit_id, kind, name, position, media_ref, text, now_iso()),
            )
            cursor.execute("UPDATE kits SET updated_at = ? WHERE id = ?", (now_iso(), kit_id))
        return self.get_item(kit_id, item_id)

    def update_item(self, item: KitItem) -> KitItem:
        with get_cursor() as cursor:
            cursor.execute(
                "UPDATE kit_items SET name = ?, text = ? WHERE id = ? AND kit_id = ?",
                (item.name, item.text, item.id, item.kit_id),
            )
        self.touch(item.kit_id)
        return self.get_item(item.kit_id, item.id)

    def delete_item(self, kit_id: str, item_id: str) -> bool:
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM kit_items WHERE kit_id = ? AND id = ?", (kit_id, item_id))
            removed = cursor.rowcount > 0
            if removed:
                cursor.execute(
                    "SELECT id FROM kit_items WHERE kit_id = ? ORDER BY position ASC, created_at ASC, id ASC", (kit_id,)
                )
                for position, row in enumerate(cursor.fetchall()):
                    cursor.execute("UPDATE kit_items SET position = ? WHERE id = ?", (position, row["id"]))
        if removed:
            self.touch(kit_id)
        return removed

    def set_item_order(self, kit_id: str, item_ids: Sequence[str]) -> None:
        with get_cursor() as cursor:
            for position, item_id in enumerate(item_ids):
                cursor.execute(
                    "UPDATE kit_items SET position = ? WHERE id = ? AND kit_id = ?", (position, item_id, kit_id)
                )
        self.touch(kit_id)

    def counts_for(self, kit_ids: Sequence[str]) -> Dict[str, Dict[str, int]]:
        counts: Dict[str, Dict[str, int]] = {kit_id: {kind: 0 for kind in ITEM_KINDS} for kit_id in kit_ids}
        if not kit_ids:
            return counts
        with get_cursor() as cursor:
            cursor.execute(
                f"SELECT kit_id, kind, COUNT(*) AS count FROM kit_items WHERE kit_id IN ({_marks(kit_ids)}) "
                "GROUP BY kit_id, kind",
                list(kit_ids),
            )
            for row in cursor.fetchall():
                counts[row["kit_id"]][row["kind"]] = row["count"]
        return counts

    def uploads_by_id(self, upload_ids: Iterable[str]) -> Dict[str, Upload]:
        ids = [value for value in dict.fromkeys(upload_ids) if value]
        if not ids:
            return {}
        with get_cursor() as cursor:
            cursor.execute(f"SELECT * FROM uploads WHERE id IN ({_marks(ids)})", ids)
            return {row["id"]: Upload.from_row(row) for row in cursor.fetchall()}

    def owned_uploads(self, owner_id: str, upload_ids: Iterable[str]) -> Dict[str, Upload]:
        found = self.uploads_by_id(upload_ids)
        return {
            upload_id: upload for upload_id, upload in found.items()
            if upload.user_id == owner_id and upload.purpose == "user_upload" and upload.media_type in MEDIA_KINDS
        }

    def thumbnails_for(self, kit_ids: Sequence[str]) -> Dict[str, List[dict]]:
        thumbnails: Dict[str, List[dict]] = {kit_id: [] for kit_id in kit_ids}
        for kit_id, items in self.items_for_kits(kit_ids).items():
            media = [item for item in items if item.media_ref]
            uploads = self.uploads_by_id(item.media_ref for item in media)
            for item in media:
                upload = uploads.get(item.media_ref)
                if upload is None or len(thumbnails[kit_id]) >= THUMBNAILS_PER_KIT:
                    continue
                thumbnails[kit_id].append({"item_id": item.id, "kind": item.kind, "upload": upload})
        return thumbnails

    def usage_for_uploads(self, owner_id: str, upload_ids: Sequence[str]) -> Dict[str, List[dict]]:
        ids = [value for value in dict.fromkeys(upload_ids) if value]
        usage: Dict[str, List[dict]] = {upload_id: [] for upload_id in ids}
        if not ids:
            return usage
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT i.media_ref AS upload_id, k.id AS kit_id, k.name AS kit_name, k.handle AS handle, "
                "i.id AS item_id, i.name AS item_name "
                "FROM kit_items i JOIN kits k ON k.id = i.kit_id "
                f"WHERE k.owner_id = ? AND i.media_ref IN ({_marks(ids)}) ORDER BY k.position, i.position",
                (owner_id, *ids),
            )
            for row in cursor.fetchall():
                usage[row["upload_id"]].append({
                    "kit_id": row["kit_id"], "kit_name": row["kit_name"], "handle": row["handle"],
                    "item_id": row["item_id"], "item_name": row["item_name"],
                })
        return usage

    def duplicate_items(self, source_kit_id: str, target_kit_id: str) -> None:
        for item in self.items(source_kit_id):
            self.add_item(target_kit_id, item.kind, item.name, media_ref=item.media_ref, text=item.text)

