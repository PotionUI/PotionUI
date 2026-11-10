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
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

let received: KeyboardEvent[] = [];

interface ShippedBinding {
	action_id: string;
	key: string;
	modifiers: string;
	context: string;
}

const MIGRATIONS = join(process.cwd(), '..', 'src', 'platform', 'database', 'migrations');

function shippedDefaults(): ShippedBinding[] {
	const rows = new Map<string, ShippedBinding>();
	for (const file of readdirSync(MIGRATIONS).filter((name) => name.endsWith('.py')).sort()) {
		const text = readFileSync(join(MIGRATIONS, file), 'utf8');
		const baseline = text.match(/KEYBINDING_ROWS = \[(.*)\]/)?.[1] ?? '';
		for (const row of baseline.matchAll(/\('([a-z_]+)', '([^']*)', '([^']*)', '[^']*', '[a-z]+', '([a-z]+)'/g)) {
			rows.set(row[1], { action_id: row[1], key: row[2], modifiers: row[3], context: row[4] });
		}
		const action = text.match(/_ACTION_ID = "([a-z_]+)"/)?.[1];
		if (!action) continue;
		const seeded = text.match(/INTO keybinding_defaults[\s\S]*?VALUES \(\?, '([^']*)', '([^']*)', '[^']*', '[a-z]+', '([a-z]+)'/);
		if (seeded) rows.set(action, { action_id: action, key: seeded[1], modifiers: seeded[2], context: seeded[3] });
		else if (/DELETE FROM keybinding_defaults/.test(text)) rows.delete(action);
	}
	return [...rows.values()];
}

async function loadOnly(bindings: ShippedBinding[]) {
	keybindingsStore.reset();
	const keybindings = bindings.map((binding) => ({ ...binding, label: binding.action_id, category: 'general', enabled: true, is_custom: false }));
	vi.spyOn(api, 'getKeybindings').mockResolvedValue({ success: true, data: { keybindings } });
	await keybindingsStore.loadBindings();
}

beforeEach(async () => {
	received = [];
	resetLayerStackForTests();
	keybindingsStore.reset();
	const keybindings = [
		{ action_id: 'delete_current_generation', key: 'Delete', modifiers: '', context: 'generate', category: 'generation' },
		{ action_id: 'new_tab', key: 't', modifiers: '', context: 'generate', category: 'generation' }
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

	it('keeps every shipped default shortcut firing on its own key and only on it', async () => {
		const defaults = shippedDefaults();
		expect(defaults.map((binding) => binding.action_id)).toEqual(
			expect.arrayContaining(['show_help', 'new_tab', 'toggle_floating_workbench', 'save_session', 'delete_current_generation'])
		);
		expect(defaults.filter((binding) => binding.key.length > 1).map((binding) => binding.action_id)).toEqual(['delete_current_generation']);
		await loadOnly(defaults);
		const key = (k: string, init: KeyboardEventInit = {}) => new KeyboardEvent('keydown', { key: k, ...init });
		for (const binding of defaults) {
			keybindingsStore.registerHandler(binding.action_id, () => {});
		}
		for (const binding of defaults) {
			const own = { shiftKey: binding.modifiers.includes('shift') || binding.key === '?' };
			expect(globalBindingFor(key(binding.key, own))?.actionId, binding.action_id).toBe(binding.action_id);
			for (const extra of ['ctrlKey', 'altKey', 'metaKey'] as const) {
				expect(globalBindingFor(key(binding.key, { ...own, [extra]: true }))?.actionId, `${binding.action_id} ${extra}`).not.toBe(binding.action_id);
			}
			if (/^[a-z]$/.test(binding.key)) {
				expect(globalBindingFor(key(binding.key.toUpperCase(), { shiftKey: true }))?.actionId, `${binding.action_id} shift`).not.toBe(binding.action_id);
			}
		}
	});

	it('matches a named key someone recorded with a modifier', async () => {
		await loadOnly([{ action_id: 'recorded_enter', key: 'Enter', modifiers: 'ctrl', context: 'global' }]);
		keybindingsStore.registerHandler('recorded_enter', () => {});
		expect(globalBindingFor(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true }))?.actionId).toBe('recorded_enter');
		expect(globalBindingFor(new KeyboardEvent('keydown', { key: 'Enter' }))).toBeNull();
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
