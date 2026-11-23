import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { isPageHidden, pollWhileVisible, whenPageVisible } from './pageVisibility';

let state: 'visible' | 'hidden' = 'visible';
const listeners = new Set<() => void>();

beforeEach(() => {
	state = 'visible';
	listeners.clear();
	vi.useFakeTimers();
	vi.stubGlobal('document', {
		get visibilityState() {
			return state;
		},
		addEventListener: (_: string, fn: () => void) => listeners.add(fn),
		removeEventListener: (_: string, fn: () => void) => listeners.delete(fn)
	});
});

afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
});

function setVisibility(next: 'visible' | 'hidden') {
	state = next;
	[...listeners].forEach((fn) => fn());
}

test('isPageHidden follows visibilityState', () => {
	expect(isPageHidden()).toBe(false);
	state = 'hidden';
	expect(isPageHidden()).toBe(true);
});

test('whenPageVisible runs at once while visible', () => {
	const run = vi.fn();
	whenPageVisible(run);
	expect(run).toHaveBeenCalledTimes(1);
});

test('whenPageVisible defers to the next visible event exactly once', () => {
	state = 'hidden';
	const run = vi.fn();
	whenPageVisible(run);
	expect(run).not.toHaveBeenCalled();
	setVisibility('visible');
	setVisibility('visible');
	expect(run).toHaveBeenCalledTimes(1);
	expect(listeners.size).toBe(0);
});

test('whenPageVisible cancel drops the pending run', () => {
	state = 'hidden';
	const run = vi.fn();
	const cancel = whenPageVisible(run);
	cancel();
	setVisibility('visible');
	expect(run).not.toHaveBeenCalled();
});

test('pollWhileVisible skips ticks while hidden and catches up on return', async () => {
	const tick = vi.fn();
	const stop = pollWhileVisible(tick, 1000);
	await vi.advanceTimersByTimeAsync(3000);
	expect(tick).toHaveBeenCalledTimes(3);
	state = 'hidden';
	await vi.advanceTimersByTimeAsync(5000);
	expect(tick).toHaveBeenCalledTimes(3);
	setVisibility('visible');
	await vi.advanceTimersByTimeAsync(0);
	expect(tick).toHaveBeenCalledTimes(4);
	stop();
	await vi.advanceTimersByTimeAsync(5000);
	expect(tick).toHaveBeenCalledTimes(4);
	expect(listeners.size).toBe(0);
});

test('pollWhileVisible never overlaps a slow tick, including the resume tick', async () => {
	let release: () => void = () => {};
	const tick = vi.fn(() => new Promise<void>((resolve) => (release = resolve)));
	const stop = pollWhileVisible(tick, 1000);
	await vi.advanceTimersByTimeAsync(1000);
	expect(tick).toHaveBeenCalledTimes(1);
	await vi.advanceTimersByTimeAsync(3000);
	setVisibility('visible');
	await vi.advanceTimersByTimeAsync(0);
	expect(tick).toHaveBeenCalledTimes(1);
	release();
	await vi.advanceTimersByTimeAsync(1000);
	expect(tick).toHaveBeenCalledTimes(2);
	release();
	stop();
});
