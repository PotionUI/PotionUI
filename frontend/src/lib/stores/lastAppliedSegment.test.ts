import { describe, it, expect } from 'vitest';
import { get } from 'svelte/store';
import { lastAppliedSegment } from './lastAppliedSegment';

describe('stores/lastAppliedSegment', () => {
	it('starts empty', () => {
		expect(get(lastAppliedSegment)).toBeNull();
	});

	it('set records the segment id with a bumped nonce', () => {
		lastAppliedSegment.set('seg-a');
		const first = get(lastAppliedSegment);
		expect(first?.segmentId).toBe('seg-a');

		lastAppliedSegment.set('seg-b');
		const second = get(lastAppliedSegment);
		expect(second?.segmentId).toBe('seg-b');
		expect(second?.nonce).toBeGreaterThan(first!.nonce);
	});

	it('re-applying the same segment id still bumps the nonce', () => {
		lastAppliedSegment.set('seg-same');
		const first = get(lastAppliedSegment)!;

		lastAppliedSegment.set('seg-same');
		const second = get(lastAppliedSegment)!;

		expect(second.segmentId).toBe('seg-same');
		expect(second.nonce).toBeGreaterThan(first.nonce);
	});

	it('clear() empties the store unconditionally', () => {
		lastAppliedSegment.set('seg-x');
		lastAppliedSegment.clear();
		expect(get(lastAppliedSegment)).toBeNull();
	});

	it('clear(segmentId) only clears a matching entry', () => {
		lastAppliedSegment.set('seg-keep');
		lastAppliedSegment.clear('seg-other');
		expect(get(lastAppliedSegment)?.segmentId).toBe('seg-keep');

		lastAppliedSegment.clear('seg-keep');
		expect(get(lastAppliedSegment)).toBeNull();
	});
});
