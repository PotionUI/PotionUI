/**
 * Reorder arithmetic for the multi-item field.
 *
 * Order is not cosmetic here: item 1 becomes `<Picture 1>` downstream, so a
 * drop that lands one slot off silently changes what the model is told. Two
 * things make the arithmetic non-obvious:
 *
 * - A drop reports the index of the tile it landed ON, which is an index in
 *   the list BEFORE the dragged item was pulled out of it.
 * - When the field renders lanes (images / video / audio), a lane's tiles are
 *   numbered 1..n within the lane, but the value is one flat array. Reordering
 *   inside a lane must not disturb the slots the other lanes occupy.
 */

import type { MediaKind } from './mediaLoaderConfig';

/**
 * Moves the item at `from` so that it ENDS UP at index `to` of the result —
 * "drop it on slot 3 and it becomes item 3". The alternative reading (insert
 * before the target) is off by one in the direction of travel and puts a tile
 * dragged rightwards one place short of where it was dropped.
 */
export function moveItem<T>(list: readonly T[], from: number, to: number): T[] {
	const next = list.slice();
	if (from < 0 || from >= next.length) return next;
	const target = Math.max(0, Math.min(next.length - 1, to));
	if (target === from) return next;
	const [item] = next.splice(from, 1);
	next.splice(target, 0, item);
	return next;
}

export interface MediaLane<T> {
	kind: MediaKind;
	items: T[];
	/** Where each lane item sits in the flat value array. */
	indices: number[];
}

/**
 * Splits the flat value into one lane per accepted kind, preserving the flat
 * array's relative order inside each lane. Kinds with no items still get a
 * lane, so the lane's "add" tile and its own limits stay visible.
 */
export function groupIntoLanes<T>(
	items: readonly T[],
	kinds: readonly MediaKind[],
	kindOf: (item: T) => MediaKind | null
): MediaLane<T>[] {
	const lanes = kinds.map((kind) => ({ kind, items: [] as T[], indices: [] as number[] }));
	const byKind = new Map(lanes.map((lane) => [lane.kind, lane]));

	items.forEach((item, index) => {
		const kind = kindOf(item);
		const lane = kind ? byKind.get(kind) : undefined;
		if (!lane) return;
		lane.items.push(item);
		lane.indices.push(index);
	});

	return lanes;
}

/**
 * Reorders one lane and writes it back into the flat array.
 *
 * The lane keeps the same set of flat slots it already occupied; only which
 * item sits in which of them changes. Anything the lane doesn't own — items of
 * another kind, or an entry whose kind couldn't be read — is untouched.
 */
export function moveWithinLane<T>(
	items: readonly T[],
	laneIndices: readonly number[],
	from: number,
	to: number
): T[] {
	if (from < 0 || from >= laneIndices.length) return items.slice();
	const laneItems = laneIndices.map((index) => items[index]);
	const reordered = moveItem(laneItems, from, to);

	const next = items.slice();
	laneIndices.forEach((flatIndex, laneIndex) => {
		next[flatIndex] = reordered[laneIndex];
	});
	return next;
}

/**
 * Drag state a tile grid needs: the tile being dragged, the tile it is
 * currently over, and which lane both belong to. Cross-lane drops are refused
 * — a video cannot become an image by being dropped in the image lane.
 */
export interface DragState {
	laneKey: string | null;
	fromIndex: number | null;
	overIndex: number | null;
}

export const NO_DRAG: DragState = { laneKey: null, fromIndex: null, overIndex: null };

export function isDropAllowed(drag: DragState, laneKey: string, index: number): boolean {
	return drag.laneKey === laneKey && drag.fromIndex !== null && drag.fromIndex !== index;
}
