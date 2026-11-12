import { describe, expect, it } from 'vitest';
import {
	acceptedOnlyReason,
	canAdd,
	isAtLimit,
	isWholeKitKey,
	itemDim,
	kindsPhrase,
	kitCardDim,
	kitIdOfWholeKey,
	kitItemKey,
	limitReason,
	nextFocusIndex,
	noKindInKitReason,
	roomLeft,
	selectedBySource,
	selectedCountText,
	sourceOfKey,
	splitKeys,
	toggleWithLimit,
	usedCount,
	wholeKitBlocked,
	wholeKitKey,
	wholeKitLabel
} from './pickerSelection';

const fit = (counts: Record<string, number>) => (id: string) => counts[id] ?? 0;

describe('keys', () => {
	it('tells the three sources apart by key shape', () => {
		expect(sourceOfKey('g1:7')).toBe('history');
		expect(sourceOfKey('3f9c2a')).toBe('library');
		expect(sourceOfKey(kitItemKey('ki1'))).toBe('kits');
		expect(sourceOfKey(wholeKitKey('kt_1'))).toBe('kits');
	});

	it('round-trips a whole-kit key', () => {
		const key = wholeKitKey('kt_9');
		expect(isWholeKitKey(key)).toBe(true);
		expect(kitIdOfWholeKey(key)).toBe('kt_9');
		expect(isWholeKitKey(kitItemKey('ki1'))).toBe(false);
	});

	it('counts the selection per source and splits whole kits from items', () => {
		const keys = ['g1:1', 'g1:2', 'u1', kitItemKey('ki1'), wholeKitKey('kt_1')];
		expect(selectedBySource(keys)).toEqual({ history: 2, library: 1, kits: 2 });
		expect(splitKeys(keys)).toEqual({ itemKeys: ['g1:1', 'g1:2', 'u1', 'kit:ki1'], kitIds: ['kt_1'] });
	});
});

describe('limit arithmetic', () => {
	it('counts a whole kit as its acceptable items', () => {
		expect(usedCount(['g1:1', wholeKitKey('a'), wholeKitKey('b')], fit({ a: 3, b: 0 }))).toBe(4);
	});

	it('has unlimited room when there is no cap', () => {
		expect(roomLeft(null, 50)).toBe(Number.POSITIVE_INFINITY);
		expect(isAtLimit(null, 50)).toBe(false);
		expect(canAdd(null, 50, 20)).toBe(true);
	});

	it('floors the room at zero and detects the limit', () => {
		expect(roomLeft(3, 5)).toBe(0);
		expect(roomLeft(3, 1)).toBe(2);
		expect(isAtLimit(3, 3)).toBe(true);
		expect(isAtLimit(3, 2)).toBe(false);
		expect(canAdd(3, 2, 2)).toBe(false);
		expect(canAdd(3, 1, 2)).toBe(true);
	});

	it('toggles within the limit and refuses a key that would overflow', () => {
		const sizes = fit({ a: 2 });
		expect(toggleWithLimit([], 'g1:1', 2, sizes)).toEqual(['g1:1']);
		expect(toggleWithLimit(['g1:1'], 'g1:2', 2, sizes)).toEqual(['g1:1', 'g1:2']);
		expect(toggleWithLimit(['g1:1', 'g1:2'], 'g1:3', 2, sizes)).toEqual(['g1:1', 'g1:2']);
		expect(toggleWithLimit(['g1:1', 'g1:2'], 'g1:2', 2, sizes)).toEqual(['g1:1']);
	});

	it('refuses a whole kit that does not fit, and one with nothing acceptable', () => {
		const sizes = fit({ big: 3, none: 0, small: 1 });
		expect(toggleWithLimit(['g1:1'], wholeKitKey('big'), 3, sizes)).toEqual(['g1:1']);
		expect(toggleWithLimit([], wholeKitKey('none'), 3, sizes)).toEqual([]);
		expect(toggleWithLimit(['g1:1'], wholeKitKey('small'), 3, sizes)).toEqual(['g1:1', wholeKitKey('small')]);
	});

	it('always lets a selected key be removed, even over the limit', () => {
		expect(toggleWithLimit(['g1:1', 'g1:2', 'g1:3'], 'g1:3', 2, fit({}))).toEqual(['g1:1', 'g1:2']);
	});
});

describe('dimming and reasons', () => {
	it('words the kinds a caller takes', () => {
		expect(kindsPhrase(['image'])).toBe('images');
		expect(kindsPhrase(['image', 'video'])).toBe('images and videos');
		expect(kindsPhrase(['image', 'video', 'audio'])).toBe('images, videos and audio');
		expect(acceptedOnlyReason(['video'])).toBe('Videos only');
		expect(noKindInKitReason(['video'])).toBe('No videos in this kit');
		expect(limitReason(9)).toBe('Limit of 9 reached');
	});

	const base = { allowed: true, acceptedKinds: ['image'] as const, selected: false, multiple: true, max: 2, used: 0 };

	it('dims a kind the caller cannot take, in single and multi', () => {
		expect(itemDim({ ...base, allowed: false })).toEqual({ disabled: true, reason: 'Images only' });
		expect(itemDim({ ...base, allowed: false, multiple: false })).toEqual({ disabled: true, reason: 'Images only' });
	});

	it('dims unselected items at the limit but never selected ones, and never in single mode', () => {
		expect(itemDim({ ...base, used: 2 })).toEqual({ disabled: true, reason: 'Limit of 2 reached' });
		expect(itemDim({ ...base, used: 2, selected: true })).toEqual({ disabled: false, reason: '' });
		expect(itemDim({ ...base, used: 2, multiple: false })).toEqual({ disabled: false, reason: '' });
		expect(itemDim({ ...base, max: null, used: 99 })).toEqual({ disabled: false, reason: '' });
	});

	it('dims a kit with nothing acceptable, and one that overflows the room', () => {
		const kit = { ...base, fit: 0 };
		expect(kitCardDim(kit)).toEqual({ disabled: true, reason: 'No images in this kit' });
		expect(kitCardDim({ ...base, fit: 3, used: 0 })).toEqual({ disabled: true, reason: 'Limit of 2 reached' });
		expect(kitCardDim({ ...base, fit: 2, used: 0 })).toEqual({ disabled: false, reason: '' });
		expect(kitCardDim({ ...base, fit: 3, selected: true })).toEqual({ disabled: false, reason: '' });
		expect(kitCardDim({ ...base, fit: 3, multiple: false })).toEqual({ disabled: false, reason: '' });
	});

	it('labels and blocks the whole-kit button', () => {
		expect(wholeKitLabel(3, ['image'])).toBe('3 images');
		expect(wholeKitLabel(1, ['image'])).toBe('1 image');
		expect(wholeKitLabel(2, ['image', 'video'])).toBe('2 items');
		expect(wholeKitBlocked(0, null, 0)).toBe(true);
		expect(wholeKitBlocked(3, 2, 0)).toBe(true);
		expect(wholeKitBlocked(2, 2, 0)).toBe(false);
	});

	it('words the selection count', () => {
		expect(selectedCountText(3, null)).toBe('3 selected');
		expect(selectedCountText(3, 9)).toBe('3 of 9 selected');
	});
});

describe('arrow navigation', () => {
	const grid = [
		{ left: 0, top: 0, width: 100, height: 80 },
		{ left: 110, top: 0, width: 100, height: 80 },
		{ left: 220, top: 0, width: 100, height: 80 },
		{ left: 0, top: 100, width: 100, height: 80 },
		{ left: 110, top: 100, width: 100, height: 80 }
	];

	it('moves along the row and clamps at the ends', () => {
		expect(nextFocusIndex(grid, 0, 'ArrowRight')).toBe(1);
		expect(nextFocusIndex(grid, 4, 'ArrowRight')).toBe(4);
		expect(nextFocusIndex(grid, 0, 'ArrowLeft')).toBe(0);
	});

	it('moves down and up to the nearest tile in the next row', () => {
		expect(nextFocusIndex(grid, 1, 'ArrowDown')).toBe(4);
		expect(nextFocusIndex(grid, 2, 'ArrowDown')).toBe(4);
		expect(nextFocusIndex(grid, 3, 'ArrowUp')).toBe(0);
		expect(nextFocusIndex(grid, 0, 'ArrowUp')).toBe(0);
		expect(nextFocusIndex(grid, 4, 'ArrowDown')).toBe(4);
	});

	it('jumps to the first and last tile', () => {
		expect(nextFocusIndex(grid, 2, 'Home')).toBe(0);
		expect(nextFocusIndex(grid, 2, 'End')).toBe(4);
		expect(nextFocusIndex([], 0, 'End')).toBe(-1);
		expect(nextFocusIndex(grid, -1, 'ArrowRight')).toBe(0);
	});
});
