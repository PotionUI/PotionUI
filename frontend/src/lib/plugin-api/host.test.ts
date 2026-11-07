// @vitest-environment jsdom
import { describe, it, expect, beforeAll, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { initHostApi, type PotionUIHostApi } from './host';
import {
	isKeyboardSuppressed,
	suppressKeyboard,
	resumeKeyboard,
	releaseAllKeyboardHolds
} from '$lib/services/keyboard';
import { iconPaths, getIconPath, iconLookup } from '$lib/utils/IconLibrary';
import { activeConfirm, cancelAllConfirms, settleConfirm } from '$lib/stores/confirm';

function host(): PotionUIHostApi {
	return (window as unknown as { __potionui: PotionUIHostApi }).__potionui;
}

beforeAll(() => {
	initHostApi();
});

describe('window.__potionui.confirm', () => {
	afterEach(() => cancelAllConfirms());

	it('passes the button labels to the app dialog and resolves false on cancel', async () => {
		const pending = host().confirm({ message: 'Discard the storyboard?', confirmLabel: 'Discard', cancelLabel: 'Keep it' });
		const request = get(activeConfirm);
		expect(request).toMatchObject({ message: 'Discard the storyboard?', confirmLabel: 'Discard', cancelLabel: 'Keep it' });
		settleConfirm(request!.id, false);
		await expect(pending).resolves.toBe(false);
	});
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

describe('window.__potionui.registerIcon', () => {
	const added: string[] = [];

	afterEach(() => {
		for (const name of added.splice(0)) delete iconPaths[name];
	});

	it('adds an icon that the icon lookup resolves', () => {
		added.push('my-plugin-clapper');
		expect(host().registerIcon('my-plugin-clapper', 'M4 4h16v16H4z')).toBe(true);
		expect(getIconPath('my-plugin-clapper')).toBe('M4 4h16v16H4z');
		expect(get(iconLookup)('my-plugin-clapper')).toBe('M4 4h16v16H4z');
	});

	it('copies a multi-path icon', () => {
		const paths = ['M1 1h2', 'M3 3h4'];
		added.push('my-plugin-timeline');
		expect(host().registerIcon('my-plugin-timeline', paths)).toBe(true);
		paths.push('M9 9h9');
		expect(getIconPath('my-plugin-timeline')).toEqual(['M1 1h2', 'M3 3h4']);
	});

	it('notifies mounted icons when a new icon arrives', () => {
		const seen: (string | string[] | null)[] = [];
		const stop = iconLookup.subscribe((lookup) => seen.push(lookup('my-plugin-late')));
		added.push('my-plugin-late');
		host().registerIcon('my-plugin-late', 'M2 2h20');
		stop();
		expect(seen).toEqual([null, 'M2 2h20']);
	});

	it('never replaces a core icon', () => {
		const original = iconPaths.plus;
		expect(host().registerIcon('plus', 'M0 0h1')).toBe(false);
		expect(iconPaths.plus).toBe(original);
	});

	it('keeps the first registration of a plugin icon', () => {
		added.push('my-plugin-render');
		expect(host().registerIcon('my-plugin-render', 'M1 1h1')).toBe(true);
		expect(host().registerIcon('my-plugin-render', 'M2 2h2')).toBe(false);
		expect(getIconPath('my-plugin-render')).toBe('M1 1h1');
	});

	it('refuses an empty name or empty paths', () => {
		expect(host().registerIcon('', 'M1 1h1')).toBe(false);
		expect(host().registerIcon('my-plugin-empty', '')).toBe(false);
		expect(host().registerIcon('my-plugin-empty', [])).toBe(false);
		expect(host().registerIcon('my-plugin-empty', ['M1 1h1', ''])).toBe(false);
		expect('my-plugin-empty' in iconPaths).toBe(false);
	});
});
