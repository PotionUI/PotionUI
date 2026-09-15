import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { toasts, capToasts, toastDisplayTitle, toastsAriaLive } from './toast';
import type { Toast } from './toast';

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

	it('collapses a same-kind burst into one entry and counts it in the title', () => {
		toasts.show('success', 'Krea-2 Turbo finished', { title: 'Generation complete' });
		toasts.show('success', 'SDXL Lightning finished', { title: 'Generation complete' });
		toasts.show('success', 'LTX-2 finished', { title: 'Generation complete' });
		const list = get(toasts);
		expect(list).toHaveLength(1);
		expect(list[0].count).toBe(3);
		expect(toastDisplayTitle(list[0])).toBe('3× Generation complete');
	});

	it('does not collapse toasts of a different kind', () => {
		toasts.show('success', 'a', { title: 'Generation complete' });
		toasts.show('error', 'b', { title: 'Generation complete' });
		expect(get(toasts)).toHaveLength(2);
	});
});

describe('capToasts', () => {
	function fakeToast(id: string): Toast {
		return { id, type: 'info', message: id };
	}

	it('returns every toast uncapped when at or below the max', () => {
		const list = [fakeToast('a'), fakeToast('b')];
		expect(capToasts(list)).toEqual({ visible: list, overflowCount: 0 });
	});

	it('caps at 3 visible and reports the overflow count', () => {
		const list = ['a', 'b', 'c', 'd', 'e'].map(fakeToast);
		const result = capToasts(list);
		expect(result.visible).toHaveLength(3);
		expect(result.visible.map((t) => t.id)).toEqual(['a', 'b', 'c']);
		expect(result.overflowCount).toBe(2);
	});
});

describe('toastDisplayTitle', () => {
	it('leaves an uncounted title untouched', () => {
		expect(toastDisplayTitle({ id: '1', type: 'info', message: 'm', title: 'A title' })).toBe('A title');
	});

	it('falls back to the message when a grouped toast has no title', () => {
		expect(toastDisplayTitle({ id: '1', type: 'info', message: 'plugin enabled', count: 2 })).toBe(
			'2× plugin enabled'
		);
	});
});

describe('toastsAriaLive', () => {
	it('is polite when no toast is an error', () => {
		expect(toastsAriaLive([{ id: '1', type: 'success', message: 'm' }])).toBe('polite');
	});

	it('is assertive when any visible toast is an error', () => {
		expect(
			toastsAriaLive([
				{ id: '1', type: 'success', message: 'm' },
				{ id: '2', type: 'error', message: 'boom' }
			])
		).toBe('assertive');
	});
});
