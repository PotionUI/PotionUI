import { describe, it, expect } from 'vitest';
import { groupIntoLanes, isDropAllowed, moveItem, moveWithinLane, NO_DRAG } from './mediaLoaderReorder';
import type { MediaKind } from './mediaLoaderConfig';

describe('moveItem', () => {
	// The contract: the dragged item ENDS UP at the index it was dropped on.
	// "Insert before the target" is off by one in the direction of travel.
	it('lands the item at the index it was dropped on, moving right', () => {
		expect(moveItem(['a', 'b', 'c', 'd'], 0, 2)).toEqual(['b', 'c', 'a', 'd']);
	});

	it('lands the item at the index it was dropped on, moving left', () => {
		expect(moveItem(['a', 'b', 'c', 'd'], 3, 1)).toEqual(['a', 'd', 'b', 'c']);
	});

	it('is a no-op when the item is dropped on itself', () => {
		expect(moveItem(['a', 'b', 'c'], 1, 1)).toEqual(['a', 'b', 'c']);
	});

	it('clamps a target past the end', () => {
		expect(moveItem(['a', 'b', 'c'], 0, 99)).toEqual(['b', 'c', 'a']);
	});

	it('ignores an out-of-range source', () => {
		expect(moveItem(['a', 'b'], 5, 0)).toEqual(['a', 'b']);
	});

	it('does not mutate the input', () => {
		const original = ['a', 'b', 'c'];
		moveItem(original, 0, 2);
		expect(original).toEqual(['a', 'b', 'c']);
	});
});

interface Item {
	id: string;
	kind: MediaKind;
}

const kindOf = (item: Item) => item.kind;

const MIXED: Item[] = [
	{ id: 'i1', kind: 'image' },
	{ id: 'v1', kind: 'video' },
	{ id: 'i2', kind: 'image' },
	{ id: 'a1', kind: 'audio' },
	{ id: 'i3', kind: 'image' }
];

describe('groupIntoLanes', () => {
	it('splits the flat value into one lane per accepted kind, preserving order', () => {
		const lanes = groupIntoLanes(MIXED, ['image', 'video', 'audio'], kindOf);
		expect(lanes.map((lane) => lane.kind)).toEqual(['image', 'video', 'audio']);
		expect(lanes[0].items.map((i) => i.id)).toEqual(['i1', 'i2', 'i3']);
		expect(lanes[0].indices).toEqual([0, 2, 4]);
		expect(lanes[1].indices).toEqual([1]);
	});

	it('keeps a lane for an accepted kind with nothing in it', () => {
		const lanes = groupIntoLanes([MIXED[0]], ['image', 'audio'], kindOf);
		expect(lanes[1]).toEqual({ kind: 'audio', items: [], indices: [] });
	});

	it('drops an item whose kind is not one the field accepts', () => {
		const lanes = groupIntoLanes(MIXED, ['image'], kindOf);
		expect(lanes).toHaveLength(1);
		expect(lanes[0].items.map((i) => i.id)).toEqual(['i1', 'i2', 'i3']);
	});
});

describe('moveWithinLane', () => {
	// A lane is numbered 1..n but lives in a flat array shared with the other
	// lanes; reordering inside it must leave every other lane's slot alone.
	it('reorders within the lane without disturbing the other lanes', () => {
		const lanes = groupIntoLanes(MIXED, ['image', 'video', 'audio'], kindOf);
		const next = moveWithinLane(MIXED, lanes[0].indices, 0, 2);

		expect(next.map((i) => i.id)).toEqual(['i2', 'v1', 'i3', 'a1', 'i1']);
		// The video and audio entries are still in slots 1 and 3.
		expect(next[1].id).toBe('v1');
		expect(next[3].id).toBe('a1');
	});

	it('ignores an out-of-range lane index', () => {
		const lanes = groupIntoLanes(MIXED, ['image'], kindOf);
		expect(moveWithinLane(MIXED, lanes[0].indices, 7, 0).map((i) => i.id)).toEqual(MIXED.map((i) => i.id));
	});
});

describe('isDropAllowed', () => {
	it('refuses a drop from another lane', () => {
		expect(isDropAllowed({ laneKey: 'image', fromIndex: 0, overIndex: 1 }, 'video', 1)).toBe(false);
	});

	it('refuses a drop onto the dragged tile itself', () => {
		expect(isDropAllowed({ laneKey: 'image', fromIndex: 1, overIndex: 1 }, 'image', 1)).toBe(false);
	});

	it('allows a drop onto another tile of the same lane', () => {
		expect(isDropAllowed({ laneKey: 'image', fromIndex: 0, overIndex: 2 }, 'image', 2)).toBe(true);
	});

	it('refuses everything when no drag is in progress', () => {
		expect(isDropAllowed(NO_DRAG, 'image', 0)).toBe(false);
	});
});
