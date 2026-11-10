import { describe, it, expect, vi, beforeEach, afterEach, onTestFinished } from 'vitest';
import type { Writable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import { keybindingsStore, shortcutLabels } from '$lib/stores/keybindings';
import { init, destroy, globalBindingFor } from '$lib/services/keyboard';
import { page } from '$app/stores';
import { get } from 'svelte/store';
import { pageOwnsKeys } from '$lib/utils/shortcutGuard';
import { registerDismissLayer, resetLayerStackForTests } from '$lib/actions/layerStack';
import { selectionDeleteIntent } from '$lib/utils/deleteShortcut';

let received: KeyboardEvent[] = [];

beforeEach(async () => {
	received = [];
	resetLayerStackForTests();
	keybindingsStore.reset();
	const keybindings = [
		{ action_id: 'delete_current_generation', key: 'Delete', modifiers: '', context: 'generate', category: 'generation' },
		{ action_id: 'new_tab', key: 't', modifiers: '', context: 'generate', category: 'generation' },
		{ action_id: 'show_help', key: '?', modifiers: '', context: 'global', category: 'general' },
		{ action_id: 'search_everything', key: 'k', modifiers: 'ctrl', context: 'global', category: 'general' },
		{ action_id: 'recorded_enter', key: 'Enter', modifiers: 'ctrl', context: 'global', category: 'general' }
	].map((binding) => ({ ...binding, label: binding.action_id, enabled: true, is_custom: false }));
	vi.spyOn(api, 'getKeybindings').mockResolvedValue({ success: true, data: { keybindings } });
	await keybindingsStore.loadBindings();
	keybindingsStore.registerHandler('delete_current_generation', (event) => received.push(event));
	init();
});

afterEach(() => {
	destroy();
	keybindingsStore.reset();
	resetLayerStackForTests();
	vi.restoreAllMocks();
	document.body.innerHTML = '';
});

function press(target: EventTarget, key: string, init: KeyboardEventInit = {}): KeyboardEvent {
	const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, composed: true, ...init });
	target.dispatchEvent(event);
	return event;
}

function field(html: string): HTMLElement {
	const wrap = document.body.appendChild(document.createElement('div'));
	wrap.innerHTML = html;
	const el = wrap.firstElementChild as HTMLElement;
	el.focus();
	return el;
}

describe('the Delete shortcut on the generate page', () => {
	it('runs with the key event when the page has focus', () => {
		const event = press(document.body, 'Delete');
		expect(received).toEqual([event]);
		expect(event.defaultPrevented).toBe(true);
	});

	it('matches the stored key whatever its case', () => {
		expect(globalBindingFor(new KeyboardEvent('keydown', { key: 'Delete' }))?.actionId).toBe('delete_current_generation');
		expect(globalBindingFor(new KeyboardEvent('keydown', { key: 'Backspace' }))).toBeNull();
	});

	it('keeps letter, symbol and modifier bindings matching, and matches other named keys as recorded', () => {
		const key = (k: string, init: KeyboardEventInit = {}) => new KeyboardEvent('keydown', { key: k, ...init });
		for (const actionId of ['new_tab', 'show_help', 'search_everything', 'recorded_enter']) {
			keybindingsStore.registerHandler(actionId, () => {});
		}
		expect(globalBindingFor(key('t'))?.actionId).toBe('new_tab');
		expect(globalBindingFor(key('T', { shiftKey: true }))).toBeNull();
		expect(globalBindingFor(key('?', { shiftKey: true }))?.actionId).toBe('show_help');
		expect(globalBindingFor(key('k', { ctrlKey: true }))?.actionId).toBe('search_everything');
		expect(globalBindingFor(key('k'))).toBeNull();
		expect(globalBindingFor(key('Enter', { ctrlKey: true }))?.actionId).toBe('recorded_enter');
		expect(globalBindingFor(key('Enter'))).toBeNull();
		expect(globalBindingFor(key('Delete', { ctrlKey: true }))).toBeNull();
	});

	it('shows as Delete in the shortcuts list and tooltips', () => {
		expect(get(shortcutLabels)['delete_current_generation']).toBe('Delete');
	});

	it('never runs while typing in an input, a textarea or a contenteditable', () => {
		for (const html of ['<input type="text" />', '<textarea></textarea>', '<div contenteditable="true" tabindex="0"></div>']) {
			press(field(html), 'Delete');
		}
		expect(received).toEqual([]);
	});

	it('does not run on other pages', () => {
		const route = page as unknown as Writable<{ url: URL }>;
		route.update((p) => ({ ...p, url: new URL('http://localhost/history') }));
		onTestFinished(() => route.update((p) => ({ ...p, url: new URL('http://localhost/generate') })));
		press(document.body, 'Delete');
		expect(received).toEqual([]);
	});

	it('hands the page an event it may not act on while a dialog, menu or overlay is open', () => {
		const event = press(document.body, 'Delete');
		expect(pageOwnsKeys(event)).toBe(true);
		const overlay = document.body.appendChild(document.createElement('div'));
		registerDismissLayer({ node: overlay });
		const second = press(document.body, 'Delete');
		expect(pageOwnsKeys(second)).toBe(false);
	});
});

describe('Delete on History and Library with real key events', () => {
	it('opens nothing from inside a text field, and nothing over an open dialog', () => {
		const input = field('<input type="search" />');
		const typed = new KeyboardEvent('keydown', { key: 'Delete', bubbles: true });
		input.dispatchEvent(typed);
		expect(selectionDeleteIntent(typed, pageOwnsKeys(typed), ['a'])).toBeNull();

		input.blur();
		const free = new KeyboardEvent('keydown', { key: 'Delete', bubbles: true });
		document.body.dispatchEvent(free);
		expect(selectionDeleteIntent(free, pageOwnsKeys(free), ['a'])).toEqual({ kind: 'single', id: 'a' });

		registerDismissLayer({ node: document.body.appendChild(document.createElement('div')) });
		const covered = new KeyboardEvent('keydown', { key: 'Delete', bubbles: true });
		document.body.dispatchEvent(covered);
		expect(selectionDeleteIntent(covered, pageOwnsKeys(covered), ['a', 'b'])).toBeNull();
	});
});
