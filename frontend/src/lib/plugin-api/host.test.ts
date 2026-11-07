// @vitest-environment jsdom
import { describe, it, expect, beforeAll, afterEach } from 'vitest';
import { initHostApi, type PotionUIHostApi } from './host';
import {
	isKeyboardSuppressed,
	suppressKeyboard,
	resumeKeyboard,
	releaseAllKeyboardHolds
} from '$lib/services/keyboard';

function host(): PotionUIHostApi {
	return (window as unknown as { __potionui: PotionUIHostApi }).__potionui;
}

beforeAll(() => {
	initHostApi();
});

describe('window.__potionui.keyboard', () => {
	afterEach(() => {
		releaseAllKeyboardHolds();
		resumeKeyboard();
	});

	it('silences the host shortcuts between suppress and resume', () => {
		const keyboard = host().keyboard;
		expect(isKeyboardSuppressed()).toBe(false);
		keyboard.suppress();
		expect(isKeyboardSuppressed()).toBe(true);
		keyboard.resume();
		expect(isKeyboardSuppressed()).toBe(false);
	});

	it('counts suppressions so one resume does not cancel another', () => {
		const keyboard = host().keyboard;
		keyboard.suppress();
		keyboard.suppress();
		keyboard.resume();
		expect(isKeyboardSuppressed()).toBe(true);
		keyboard.resume();
		expect(isKeyboardSuppressed()).toBe(false);
		keyboard.resume();
		keyboard.suppress();
		expect(isKeyboardSuppressed()).toBe(true);
	});

	it('returns a release that only drops its own suppression, once', () => {
		const keyboard = host().keyboard;
		const releaseFirst = keyboard.suppress();
		keyboard.suppress();
		releaseFirst();
		releaseFirst();
		expect(isKeyboardSuppressed()).toBe(true);
		keyboard.resume();
		expect(isKeyboardSuppressed()).toBe(false);
	});

	it('balances suppressions released by resume and by their own release', () => {
		const keyboard = host().keyboard;
		keyboard.suppress();
		const releaseSecond = keyboard.suppress();
		keyboard.resume();
		releaseSecond();
		expect(isKeyboardSuppressed()).toBe(false);
	});

	it('does not interfere with the core suppression flag', () => {
		const keyboard = host().keyboard;
		keyboard.suppress();
		resumeKeyboard();
		expect(isKeyboardSuppressed()).toBe(true);
		keyboard.resume();
		suppressKeyboard();
		keyboard.resume();
		expect(isKeyboardSuppressed()).toBe(true);
		resumeKeyboard();
		expect(isKeyboardSuppressed()).toBe(false);
	});

	it('is released when the plugin page host clears every hold', () => {
		host().keyboard.suppress();
		host().keyboard.suppress();
		releaseAllKeyboardHolds();
		expect(isKeyboardSuppressed()).toBe(false);
	});

	it('ignores a release left over from a page that was already cleared', () => {
		const stale = host().keyboard.suppress();
		releaseAllKeyboardHolds();
		host().keyboard.suppress();
		stale();
		expect(isKeyboardSuppressed()).toBe(true);
	});
});
