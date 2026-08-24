import { describe, it, expect } from 'vitest';
import { get } from 'svelte/store';
import { appliedSegmentActions, isAppliedSegmentAction } from './appliedSegmentActions';

describe('stores/appliedSegmentActions', () => {
	it('starts empty', () => {
		expect(get(appliedSegmentActions)).toEqual({});
	});

	it('set records the applying card for a segment id', () => {
		appliedSegmentActions.set('seg-a', 'msg-1', 0);
		const map = get(appliedSegmentActions);
		expect(map['seg-a']).toMatchObject({ messageId: 'msg-1', actionIndex: 0 });
	});

	it('isAppliedSegmentAction matches the applying (messageId, actionIndex)', () => {
		appliedSegmentActions.set('seg-b', 'msg-2', 1);
		const map = get(appliedSegmentActions);
		expect(isAppliedSegmentAction(map, 'msg-2', 1)).toBe(true);
		expect(isAppliedSegmentAction(map, 'msg-2', 0)).toBe(false);
		expect(isAppliedSegmentAction(map, 'msg-other', 1)).toBe(false);
	});

	it('applying a different variant of the same segment moves the marker', () => {
		appliedSegmentActions.set('seg-c', 'msg-3', 0);
		let map = get(appliedSegmentActions);
		expect(isAppliedSegmentAction(map, 'msg-3', 0)).toBe(true);

		// A second variant card for the same segment (same message, different action index)
		appliedSegmentActions.set('seg-c', 'msg-3', 2);
		map = get(appliedSegmentActions);
		expect(isAppliedSegmentAction(map, 'msg-3', 0)).toBe(false);
		expect(isAppliedSegmentAction(map, 'msg-3', 2)).toBe(true);
	});

	it('re-applying the same card still bumps the nonce', () => {
		appliedSegmentActions.set('seg-d', 'msg-4', 0);
		const first = get(appliedSegmentActions)['seg-d'];

		appliedSegmentActions.set('seg-d', 'msg-4', 0);
		const second = get(appliedSegmentActions)['seg-d'];

		expect(second.nonce).toBeGreaterThan(first.nonce);
	});

	it('clear(segmentId) removes only that entry', () => {
		appliedSegmentActions.set('seg-e', 'msg-5', 0);
		appliedSegmentActions.set('seg-f', 'msg-5', 1);

		appliedSegmentActions.clear('seg-e');
		const map = get(appliedSegmentActions);
		expect(map['seg-e']).toBeUndefined();
		expect(map['seg-f']).toBeDefined();
	});

	it('clear() with no argument empties every entry', () => {
		appliedSegmentActions.set('seg-g', 'msg-6', 0);
		appliedSegmentActions.clear();
		expect(get(appliedSegmentActions)).toEqual({});
	});
});
