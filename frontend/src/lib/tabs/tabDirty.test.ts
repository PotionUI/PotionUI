import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { GenerationTab } from '$lib/types/tabs';

const guard = vi.hoisted(() => ({ dirty: false, calls: 0 }));

vi.mock('$lib/utils/unsavedChangesGuard', () => ({
	tabHasUnsavedSessionChanges: () => {
		guard.calls += 1;
		return guard.dirty;
	}
}));

const { tabShowsUnsavedChanges } = await import('./tabDirty');

function tab(id: string): GenerationTab {
	return { id } as GenerationTab;
}

beforeEach(() => {
	guard.dirty = false;
	guard.calls = 0;
});

describe('tabShowsUnsavedChanges', () => {
	it('trusts the live session owner over the stored check, both ways', () => {
		guard.dirty = true;
		expect(tabShowsUnsavedChanges(tab('a'), { a: false })).toBe(false);
		guard.dirty = false;
		expect(tabShowsUnsavedChanges(tab('a'), { a: true })).toBe(true);
		expect(guard.calls).toBe(0);
	});

	it('falls back to the stored check for a tab without a live owner', () => {
		guard.dirty = true;
		expect(tabShowsUnsavedChanges(tab('b'), { a: false })).toBe(true);
		expect(guard.calls).toBe(1);
	});

	it('checks each tab object once and again only after the tab is replaced', () => {
		const first = tab('c');
		guard.dirty = true;
		expect(tabShowsUnsavedChanges(first, {})).toBe(true);
		guard.dirty = false;
		expect(tabShowsUnsavedChanges(first, {})).toBe(true);
		expect(guard.calls).toBe(1);
		const replaced = { ...first };
		expect(tabShowsUnsavedChanges(replaced, {})).toBe(false);
		expect(guard.calls).toBe(2);
	});
});
