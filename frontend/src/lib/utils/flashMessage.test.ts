import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { createFlashMessage } from './flashMessage';

describe('createFlashMessage', () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('starts empty', () => {
		const { message } = createFlashMessage();
		expect(get(message)).toBeNull();
	});

	it('sets the message on flash', () => {
		const { message, flash } = createFlashMessage();
		flash('Added 3 to collection');
		expect(get(message)).toBe('Added 3 to collection');
	});

	it('clears the message after the duration elapses', () => {
		const { message, flash } = createFlashMessage(3000);
		flash('Export started');
		vi.advanceTimersByTime(2999);
		expect(get(message)).toBe('Export started');
		vi.advanceTimersByTime(1);
		expect(get(message)).toBeNull();
	});

	it('restarts the timer on a follow-up flash instead of racing the old one', () => {
		const { message, flash } = createFlashMessage(3000);
		flash('First');
		vi.advanceTimersByTime(2000);
		flash('Second');
		vi.advanceTimersByTime(2000);
		expect(get(message)).toBe('Second');
		vi.advanceTimersByTime(1000);
		expect(get(message)).toBeNull();
	});
});
