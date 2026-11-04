import { expect, test } from 'vitest';
import { OPT_OUT_SELECTOR, nodeIdForKey, shouldOpenSettings } from './nodeClick';

const target = (blocking: unknown = null) => ({
	closest(selector: string) {
		expect(selector).toBe(OPT_OUT_SELECTOR);
		return blocking;
	}
});
const click = (extra: Record<string, unknown> = {}) => ({ button: 0, defaultPrevented: false, target: target(), ...extra });

test('a plain click on the node body opens settings', () => {
	expect(shouldOpenSettings(click())).toBe(true);
});

test('a click on or inside an interactive element does not open settings', () => {
	expect(shouldOpenSettings(click({ target: target({ tagName: 'BUTTON' }) }))).toBe(false);
});

test('the opt-out list covers controls, tiles, checkboxes and explicit opt-outs', () => {
	for (const part of ['button', 'a', 'input', 'select', 'textarea', 'label', '[role="button"]', '[role="checkbox"]', '[contenteditable]', '[data-no-open]']) {
		expect(OPT_OUT_SELECTOR.split(', ').some((entry) => entry.startsWith(part))).toBeTruthy();
	}
});

test('selected text, modifier keys, other buttons and handled events do not open settings', () => {
	expect(shouldOpenSettings(click(), 'some words')).toBe(false);
	expect(shouldOpenSettings(click(), '   ')).toBe(true);
	expect(shouldOpenSettings(click({ shiftKey: true }))).toBe(false);
	expect(shouldOpenSettings(click({ ctrlKey: true }))).toBe(false);
	expect(shouldOpenSettings(click({ button: 2 }))).toBe(false);
	expect(shouldOpenSettings(click({ defaultPrevented: true }))).toBe(false);
});

test('a target without closest does not open settings', () => {
	expect(shouldOpenSettings(click({ target: {} }))).toBe(false);
	expect(shouldOpenSettings(click({ target: null }))).toBe(false);
	expect(shouldOpenSettings(null)).toBe(false);
});

test('Enter on a focused card yields its id', () => {
	const card = { classList: { contains: () => false }, dataset: { nodeRoot: 'n2' } };
	expect(nodeIdForKey({ key: 'Enter', target: card })).toBe('n2');
});

test('Enter on a child control, other keys and modified Enter yield nothing', () => {
	const child = { classList: { contains: () => false }, dataset: {} };
	const card = { classList: { contains: () => false }, dataset: { nodeRoot: 'n1' } };
	expect(nodeIdForKey({ key: 'Enter', target: child })).toBe(null);
	expect(nodeIdForKey({ key: ' ', target: card })).toBe(null);
	expect(nodeIdForKey({ key: 'Enter', target: card, ctrlKey: true })).toBe(null);
});
