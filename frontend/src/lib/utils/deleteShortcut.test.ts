import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { generationTabs, type GenerationTab } from '$lib/types/tabs';
import {
	generateDeleteIntent,
	isDeleteKey,
	selectionDeleteIntent,
	type DeleteKeyEvent,
	type GenerateDeleteState
} from './deleteShortcut';

function key(name: string, init: Partial<DeleteKeyEvent> = {}): DeleteKeyEvent {
	return {
		key: name,
		ctrlKey: false,
		metaKey: false,
		altKey: false,
		shiftKey: false,
		defaultPrevented: false,
		repeat: false,
		...init
	};
}

const DEL = key('Delete');

function tabWith(generationId: string | null): GenerationTab {
	tabsStore.reset();
	const tab = generationTabs(get(tabsStore).tabs)[0];
	tabsStore.updateTab(tab.id, {
		generation: { ...tab.generation, currentGeneration: generationId ? { id: generationId, status: 'completed' } : null }
	});
	return generationTabs(get(tabsStore).tabs)[0];
}

describe('the delete key', () => {
	it('is the plain Delete key only', () => {
		expect(isDeleteKey(DEL)).toBe(true);
		for (const other of ['Backspace', 'd', 'Del', 'Escape']) expect(isDeleteKey(key(other))).toBe(false);
	});

	it('is not Delete with a modifier, a held-down repeat, or a key something else already handled', () => {
		for (const init of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }, { shiftKey: true }, { repeat: true }, { defaultPrevented: true }]) {
			expect(isDeleteKey(key('Delete', init))).toBe(false);
		}
	});
});

describe('Delete on a page with a selection', () => {
	it('does nothing with nothing selected', () => {
		expect(selectionDeleteIntent(DEL, true, [])).toBeNull();
	});

	it('does nothing while typing or while a dialog, menu or overlay is open', () => {
		expect(selectionDeleteIntent(DEL, false, ['a'])).toBeNull();
		expect(selectionDeleteIntent(DEL, false, ['a', 'b'])).toBeNull();
	});

	it('does nothing for any other key', () => {
		expect(selectionDeleteIntent(key('Backspace'), true, ['a'])).toBeNull();
		expect(selectionDeleteIntent(key('Delete', { ctrlKey: true }), true, ['a'])).toBeNull();
	});

	it('asks for the single-item dialog with one selected', () => {
		expect(selectionDeleteIntent(DEL, true, ['a'])).toEqual({ kind: 'single', id: 'a' });
		expect(selectionDeleteIntent(DEL, true, ['a', 'a'])).toEqual({ kind: 'single', id: 'a' });
	});

	it('asks for the bulk dialog with several selected', () => {
		expect(selectionDeleteIntent(DEL, true, ['a', 'b', 'c'])).toEqual({ kind: 'bulk', ids: ['a', 'b', 'c'] });
	});
});

describe('Delete on the generate page', () => {
	beforeEach(() => tabsStore.reset());

	function state(overrides: Partial<GenerateDeleteState> = {}): GenerateDeleteState {
		return {
			ownsKeys: true,
			repeat: false,
			toolTabActive: false,
			showsGrid: false,
			deleteInFlight: false,
			tab: tabWith('gen-1'),
			...overrides
		};
	}

	it('targets the generation shown in the selected tab', () => {
		const s = state();
		expect(generateDeleteIntent(s)).toEqual({ tabId: s.tab!.id, generationId: 'gen-1' });
	});

	it('does nothing when the tab has no generation', () => {
		expect(generateDeleteIntent(state({ tab: tabWith(null) }))).toBeNull();
		expect(generateDeleteIntent(state({ tab: null }))).toBeNull();
		expect(generateDeleteIntent(state({ tab: undefined }))).toBeNull();
	});

	it('does nothing while typing or while a dialog, menu or overlay is open', () => {
		expect(generateDeleteIntent(state({ ownsKeys: false }))).toBeNull();
	});

	it('does nothing on a tool tab, on a compare grid, on a held-down repeat, or while a delete is already open', () => {
		expect(generateDeleteIntent(state({ toolTabActive: true }))).toBeNull();
		expect(generateDeleteIntent(state({ showsGrid: true }))).toBeNull();
		expect(generateDeleteIntent(state({ repeat: true }))).toBeNull();
		expect(generateDeleteIntent(state({ deleteInFlight: true }))).toBeNull();
	});
});
