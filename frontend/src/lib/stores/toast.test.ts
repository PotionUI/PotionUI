import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { toasts } from './toast';

describe('toasts store', () => {
	beforeEach(() => {
		vi.useFakeTimers();
		// Clear any residual toasts between tests.
		for (const t of get(toasts)) toasts.remove(t.id);
	});

	afterEach(() => {
		vi.runOnlyPendingTimers();
		vi.useRealTimers();
	});

	it('adds a warning toast', () => {
		toasts.warning('heads up');
		const list = get(toasts);
		expect(list).toHaveLength(1);
		expect(list[0].type).toBe('warning');
		expect(list[0].message).toBe('heads up');
	});

	it('supports an optional title via show()', () => {
		toasts.show('info', 'body text', { title: 'A title' });
		const [toast] = get(toasts);
		expect(toast.title).toBe('A title');
		expect(toast.message).toBe('body text');
		expect(toast.type).toBe('info');
	});

	it('auto-removes after the given duration', () => {
		toasts.show('success', 'gone soon', { duration: 1000 });
		expect(get(toasts)).toHaveLength(1);
		vi.advanceTimersByTime(999);
		expect(get(toasts)).toHaveLength(1);
		vi.advanceTimersByTime(1);
		expect(get(toasts)).toHaveLength(0);
	});

	it('does not auto-remove when duration is 0', () => {
		toasts.show('info', 'sticky', { duration: 0 });
		vi.advanceTimersByTime(100000);
		expect(get(toasts)).toHaveLength(1);
	});

	it('removes a toast by id', () => {
		const id = toasts.error('boom');
		expect(get(toasts)).toHaveLength(1);
		toasts.remove(id);
		expect(get(toasts)).toHaveLength(0);
	});

	it('errors default to a longer 6s duration', () => {
		toasts.error('boom');
		expect(get(toasts)).toHaveLength(1);
		vi.advanceTimersByTime(4000);
		expect(get(toasts)).toHaveLength(1);
		vi.advanceTimersByTime(2000);
		expect(get(toasts)).toHaveLength(0);
	});
});
