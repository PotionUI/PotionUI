import { describe, it, expect } from 'vitest';
import {
	buildMediaGroups,
	clampSelection,
	describeHandle,
	displayOrder,
	dropInGroup,
	formatCount,
	handleText,
	layoutGroups,
	moveSelection,
	nudgeInGroup
} from './mediaFieldGroups';
import type { MediaKind } from './mediaLoaderConfig';

type Item = { id: string; kind: MediaKind };

const kindOf = (item: Item) => item.kind;
const mixed: MediaKind[] = ['image', 'video', 'audio'];

function items(spec: string): Item[] {
	return spec.split(' ').map((token) => {
		const kind = token[0] === 'v' ? 'video' : token[0] === 'a' ? 'audio' : 'image';
		return { id: token, kind };
	});
}

describe('buildMediaGroups', () => {
	it('numbers each kind from one, whatever its place in the flat list', () => {
		const groups = buildMediaGroups(items('i1 v1 i2 a1 v2'), mixed, { maxItems: null, maxItemsByKind: {} }, kindOf);
		expect(groups.map((g) => [g.kind, g.items.map((e) => e.position)])).toEqual([
			['image', [1, 2]],
			['video', [1, 2]],
			['audio', [1]]
		]);
		expect(groups[1].items.map((e) => e.flatIndex)).toEqual([1, 4]);
	});

	it('measures each group against its own limit', () => {
		const groups = buildMediaGroups(
			items('i1 v1'),
			mixed,
			{ maxItems: null, maxItemsByKind: { image: 9, video: 1, audio: 3 } },
			kindOf
		);
		expect(groups.map((g) => [g.limit, g.full])).toEqual([
			[9, false],
			[1, true],
			[3, false]
		]);
	});

	it('falls back to the scalar max for a kind without its own limit', () => {
		const groups = buildMediaGroups(items('i1 i2'), ['image'], { maxItems: 2, maxItemsByKind: {} }, kindOf);
		expect(groups[0].full).toBe(true);
		expect(groups[0].limit).toBe(2);
	});

	it('leaves a group unbounded when nothing limits it', () => {
		const groups = buildMediaGroups(items('i1'), ['image'], { maxItems: null, maxItemsByKind: {} }, kindOf);
		expect(groups[0].limit).toBeNull();
		expect(groups[0].full).toBe(false);
	});
});

describe('layoutGroups', () => {
	const limits = { maxItems: null, maxItemsByKind: { image: 2, video: 1, audio: 1 } };

	it('draws no eyebrow for a single-kind field', () => {
		const layout = layoutGroups(buildMediaGroups(items('i1'), ['image'], limits, kindOf));
		expect(layout.eyebrows).toBe(false);
		expect(layout.visible).toHaveLength(1);
	});

	it('shows only the groups that hold something and folds the empty ones', () => {
		const layout = layoutGroups(buildMediaGroups(items('i1 i2'), mixed, limits, kindOf));
		expect(layout.eyebrows).toBe(true);
		expect(layout.visible.map((g) => g.kind)).toEqual(['image']);
		expect(layout.folded.map((g) => g.kind)).toEqual(['video', 'audio']);
	});

	it('keeps a full group visible and out of the folded line', () => {
		const layout = layoutGroups(buildMediaGroups(items('v1'), mixed, limits, kindOf));
		expect(layout.visible.map((g) => g.kind)).toEqual(['video']);
		expect(layout.folded.map((g) => g.kind)).toEqual(['image', 'audio']);
	});

	it('offers no add button for an empty group that cannot take anything', () => {
		const groups = buildMediaGroups(items('i1'), mixed, limits, kindOf).map((group) => ({ ...group, full: true }));
		const layout = layoutGroups(groups);
		expect(layout.visible.map((g) => g.kind)).toEqual(['image']);
		expect(layout.folded).toEqual([]);
	});

	it('keeps every group visible with its own add box when folding is off', () => {
		const layout = layoutGroups(buildMediaGroups(items('i1 i2'), mixed, limits, kindOf), null, false);
		expect(layout.eyebrows).toBe(true);
		expect(layout.visible.map((g) => g.kind)).toEqual(['image', 'video', 'audio']);
		expect(layout.folded).toEqual([]);
	});

	it('keeps every group visible when nothing is held and folding is off', () => {
		const layout = layoutGroups(buildMediaGroups([], mixed, limits, kindOf), null, false);
		expect(layout.visible.map((g) => g.kind)).toEqual(['image', 'video', 'audio']);
	});

	it('shows an empty group while a file of its kind is arriving', () => {
		const layout = layoutGroups(buildMediaGroups(items('i1'), mixed, limits, kindOf), 'video');
		expect(layout.visible.map((g) => g.kind)).toEqual(['image', 'video']);
		expect(layout.folded.map((g) => g.kind)).toEqual(['audio']);
	});

	it('folds every kind of an empty mixed field', () => {
		const layout = layoutGroups(buildMediaGroups([], mixed, limits, kindOf));
		expect(layout.visible).toEqual([]);
		expect(layout.folded).toHaveLength(3);
	});
});

describe('selection', () => {
	const groups = buildMediaGroups(items('v1 i1 i2'), mixed, { maxItems: null, maxItemsByKind: {} }, kindOf);

	it('lists flat indices group by group in display order', () => {
		expect(displayOrder(groups)).toEqual([1, 2, 0]);
	});

	it('moves through the display order and stops at both ends', () => {
		expect(moveSelection(groups, 1, 1)).toBe(2);
		expect(moveSelection(groups, 2, 1)).toBe(0);
		expect(moveSelection(groups, 0, 1)).toBe(0);
		expect(moveSelection(groups, 1, -1)).toBe(1);
	});

	it('starts at the first displayed item when nothing is selected', () => {
		expect(moveSelection(groups, null, 1)).toBe(1);
	});

	it('clamps a selection past the end and clears it when the list empties', () => {
		expect(clampSelection(5, 3)).toBe(2);
		expect(clampSelection(null, 3)).toBeNull();
		expect(clampSelection(0, 0)).toBeNull();
	});
});

describe('reorder inside a group', () => {
	const flat = items('i1 v1 i2 v2 i3');
	const groups = buildMediaGroups(flat, mixed, { maxItems: null, maxItemsByKind: {} }, kindOf);

	it('moves an item earlier within its kind and leaves other kinds in their slots', () => {
		const result = nudgeInGroup(flat, groups[0], 1, -1);
		expect(result?.items.map((i) => i.id)).toEqual(['i2', 'v1', 'i1', 'v2', 'i3']);
		expect(result?.flatIndex).toBe(0);
	});

	it('refuses to move past either end of the group', () => {
		expect(nudgeInGroup(flat, groups[1], 0, -1)).toBeNull();
		expect(nudgeInGroup(flat, groups[1], 1, 1)).toBeNull();
	});

	it('drops an item onto another slot of the same group and reports where it landed', () => {
		const result = dropInGroup(flat, groups[0], 0, 2);
		expect(result.items.map((i) => i.id)).toEqual(['i2', 'v1', 'i3', 'v2', 'i1']);
		expect(result.flatIndex).toBe(4);
	});
});

describe('labels', () => {
	it('writes the prompt handle in full or as a phone handle', () => {
		expect(handleText('image', 2, false)).toBe('Picture 2');
		expect(handleText('video', 1, false)).toBe('Video 1');
		expect(handleText('audio', 3, true)).toBe('A3');
		expect(handleText('image', 1, true)).toBe('P1');
	});

	it('writes a count against its limit', () => {
		expect(formatCount(3, 10)).toBe('3/10');
		expect(formatCount(3, null)).toBe('3');
	});
});

describe('describeHandle', () => {
	const spec = { field: 'refs', kind: 'video' as const, token: '<Video @>' };

	it('uses the prompt spec when the field has one and says how often the prompt uses it', () => {
		const handle = describeHandle('video', 2, spec, 3);
		expect(handle).toMatchObject({ long: 'Video 2', short: 'V2', uses: 3 });
		expect(handle.tooltip).toBe('<Video 2> in the prompt, used 3 times');
	});

	it('says an unused item is not used yet and a single use is one time', () => {
		expect(describeHandle('video', 1, spec, 0).tooltip).toBe('<Video 1> in the prompt, not used yet');
		expect(describeHandle('video', 1, spec, 1).tooltip).toBe('<Video 1> in the prompt, used 1 time');
	});

	it('falls back to the kind name and no tooltip without a spec', () => {
		expect(describeHandle('image', 4, null, 0)).toEqual({ long: 'Picture 4', short: 'P4', uses: 0, tooltip: '' });
	});
});
