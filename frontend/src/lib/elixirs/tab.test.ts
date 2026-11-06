import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';

const goto = vi.fn();
vi.mock('$app/navigation', () => ({ goto: (...args: unknown[]) => goto(...args) }));

const memory = new Map<string, string>();
vi.stubGlobal('localStorage', {
	getItem: (key: string) => memory.get(key) ?? null,
	setItem: (key: string, value: string) => void memory.set(key, value),
	removeItem: (key: string) => void memory.delete(key)
});

const { tabsStore } = await import('$lib/stores/tabs');
const { isToolTab } = await import('$lib/types/tabs');
const { closeElixirTabs, elixirIdOf, lastElixirId, openElixirChooser, openElixirIds, openElixirTab, openLastElixir, renameElixirTabs, LAST_ELIXIR_STORAGE_KEY } = await import('./tab');

function active() {
	const state = get(tabsStore);
	return state.tabs.find((tab) => tab.id === state.activeTabId);
}

describe('elixir tabs', () => {
	beforeEach(() => {
		tabsStore.reset();
		localStorage.removeItem(LAST_ELIXIR_STORAGE_KEY);
		goto.mockReset();
	});

	it('opens one tab per elixir, named after it, and remembers it', () => {
		const id = openElixirTab({ id: 'e1', name: 'Potion study' }, { navigate: false });
		expect(active()).toMatchObject({ id, tool: 'elixir', key: 'e1', state: { elixirId: 'e1' }, name: 'Potion study' });
		expect(lastElixirId()).toBe('e1');
		openElixirTab({ id: 'e2', name: 'Lookbook' }, { navigate: false });
		expect(openElixirTab({ id: 'e1' }, { navigate: false })).toBe(id);
		expect(openElixirIds()).toEqual(new Set(['e1', 'e2']));
	});

	it('reads the elixir id from the key, or from older state', () => {
		expect(elixirIdOf({ kind: 'tool', id: 't', name: 'x', tool: 'elixir', source: null, state: { elixirId: 'e9' } })).toBe('e9');
		expect(elixirIdOf({ kind: 'tool', id: 't', name: 'x', tool: 'image', source: null, state: { elixirId: 'e9' } })).toBeNull();
		expect(elixirIdOf(null)).toBeNull();
	});

	it('reuses one blank chooser tab', () => {
		const first = openElixirChooser({ navigate: false });
		tabsStore.addTab();
		expect(openElixirChooser({ navigate: false })).toBe(first);
		expect(get(tabsStore).tabs.filter(isToolTab)).toHaveLength(1);
	});

	it('the sidebar focuses the last elixir used, or opens the chooser', () => {
		const chooser = openLastElixir({ navigate: false });
		expect(active()).toMatchObject({ id: chooser, tool: 'elixir' });
		expect(elixirIdOf(active() as never)).toBeNull();
		tabsStore.reset();
		const potion = openElixirTab({ id: 'e1', name: 'Potion study' }, { navigate: false });
		const lookbook = openElixirTab({ id: 'e2', name: 'Lookbook' }, { navigate: false });
		localStorage.setItem(LAST_ELIXIR_STORAGE_KEY, 'e1');
		tabsStore.addTab();
		expect(openLastElixir({ navigate: false })).toBe(potion);
		localStorage.setItem(LAST_ELIXIR_STORAGE_KEY, 'gone');
		tabsStore.addTab();
		expect(openLastElixir({ navigate: false })).toBe(lookbook);
	});

	it('goes to Generate after opening, unless it is already there', async () => {
		vi.stubGlobal('window', { location: { pathname: '/elixirs/e1' } });
		openElixirTab({ id: 'e1' }, { replace: true });
		await new Promise((resolve) => setTimeout(resolve, 0));
		expect(goto).toHaveBeenCalledWith('/generate', { replaceState: true });
		vi.stubGlobal('window', { location: { pathname: '/generate' } });
		openElixirTab({ id: 'e2' });
		await new Promise((resolve) => setTimeout(resolve, 0));
		expect(goto).toHaveBeenCalledTimes(1);
		vi.unstubAllGlobals();
		vi.stubGlobal('localStorage', {
			getItem: (key: string) => memory.get(key) ?? null,
			setItem: (key: string, value: string) => void memory.set(key, value),
			removeItem: (key: string) => void memory.delete(key)
		});
	});

	it('renames and closes the tabs of one elixir', () => {
		const id = openElixirTab({ id: 'e1', name: 'Potion study' }, { navigate: false });
		openElixirTab({ id: 'e2', name: 'Lookbook' }, { navigate: false });
		renameElixirTabs('e1', 'Potion study II');
		expect(get(tabsStore).tabs.find((tab) => tab.id === id)?.name).toBe('Potion study II');
		closeElixirTabs('e1');
		expect(openElixirIds()).toEqual(new Set(['e2']));
	});
});
