import { describe, it, expect, beforeEach, vi } from 'vitest';

// vitest runs with `environment: 'node'` (vite.config.ts), so `$app/environment`'s
// `browser` is false and `storage` no-ops — force it true and stub `localStorage`
// (same pattern as tabsSoundSettings.test.ts) so the persist/restore round trip
// can actually be exercised.
vi.mock('$app/environment', () => ({ browser: true }));

import { ExpansionState } from './expansion.svelte';

describe('ExpansionState', () => {
	beforeEach(() => {
		const store = new Map<string, string>();
		(globalThis as any).localStorage = {
			getItem: (key: string) => store.get(key) ?? null,
			setItem: (key: string, value: string) => void store.set(key, value),
			removeItem: (key: string) => void store.delete(key),
			clear: () => store.clear()
		};
	});

	it('starts empty when no storage key is given', () => {
		const state = new ExpansionState();
		expect(state.has('a')).toBe(false);
	});

	it('toggle flips membership and always reassigns a NEW Set instance', () => {
		const state = new ExpansionState();
		const before = state.ids;

		state.toggle('a');
		expect(state.has('a')).toBe(true);
		expect(state.ids).not.toBe(before);

		const afterAdd = state.ids;
		state.toggle('a');
		expect(state.has('a')).toBe(false);
		expect(state.ids).not.toBe(afterAdd);
	});

	it('persists to and restores from localStorage under the given key', () => {
		const a = new ExpansionState('pane:test');
		a.expand('folder-1');
		a.expand('folder-2');

		const b = new ExpansionState('pane:test');
		expect(b.has('folder-1')).toBe(true);
		expect(b.has('folder-2')).toBe(true);
	});

	it('falls back to an empty set on malformed persisted JSON', () => {
		localStorage.setItem('pane:broken', '{not json');
		const state = new ExpansionState('pane:broken');
		expect(state.has('anything')).toBe(false);
	});

	it('collapse removes an id; expandMany adds several at once', () => {
		const state = new ExpansionState();
		state.expandMany(['a', 'b', 'c']);
		expect(state.has('a')).toBe(true);
		expect(state.has('b')).toBe(true);
		expect(state.has('c')).toBe(true);

		state.collapse('b');
		expect(state.has('b')).toBe(false);
		expect(state.has('a')).toBe(true);
	});
});
