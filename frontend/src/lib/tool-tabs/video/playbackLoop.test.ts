import { describe, expect, it } from 'vitest';
import { loopStep } from './playbackLoop';

describe('loopStep', () => {
	it('keeps playing inside the range', () => {
		expect(loopStep({ time: 1, rangeEnd: 3.57, loop: true, ended: false })).toBe('continue');
	});

	it('wraps at the trimmed end when looping and stops when not', () => {
		expect(loopStep({ time: 3.57, rangeEnd: 3.57, loop: true, ended: false })).toBe('wrap');
		expect(loopStep({ time: 3.57, rangeEnd: 3.57, loop: false, ended: false })).toBe('stop');
	});

	it('wraps when the media ended before the frame loop reached the end of the range', () => {
		expect(loopStep({ time: 3.5, rangeEnd: 3.57, loop: true, ended: true })).toBe('wrap');
		expect(loopStep({ time: 3.5, rangeEnd: 3.57, loop: false, ended: true })).toBe('stop');
	});
});
