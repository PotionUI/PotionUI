/**
 * Lead-file selection for workbench results and history card previews.
 *
 * A "derived" file was produced from another final file of the same generation
 * (e.g. Krea-2's inline enhance pass). Persisted file order is authoritative
 * and never reordered — derived files continue the stored index sequence — so
 * primacy is decided here, at presentation time: the newest derived item leads,
 * otherwise the first item (current behavior for generations without one).
 *
 * Accepts both spellings of the flag: `derived` on live batch items
 * (gallery_update payloads) and `is_derived` on persisted file records
 * (history payloads).
 */
export interface DerivedFlagged {
	derived?: boolean;
	is_derived?: boolean;
}

export function isDerivedItem(item: DerivedFlagged | null | undefined): boolean {
	return item?.derived === true || item?.is_derived === true;
}

/** Index of the last (newest) derived item, or 0 when none is derived. */
export function leadIndex(items: ReadonlyArray<DerivedFlagged | null | undefined>): number {
	for (let i = items.length - 1; i >= 0; i--) {
		if (isDerivedItem(items[i])) return i;
	}
	return 0;
}
