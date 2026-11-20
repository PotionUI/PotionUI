import { beforeEach, describe, expect, it } from 'vitest';
import { lastInGroups, rememberInGroup, shownInGroup } from './groupMemory';

let store: Map<string, string>;

beforeEach(() => {
	store = new Map();
	Object.defineProperty(globalThis, 'localStorage', {
		configurable: true,
		value: {
			getItem: (key: string) => store.get(key) ?? null,
			setItem: (key: string, value: string) => void store.set(key, value),
			removeItem: (key: string) => void store.delete(key)
		}
	});
});

describe('groupMemory', () => {
	it('remembers the entry last used per group and editor', () => {
		rememberInGroup('image', 'fx', 'vignette');
		rememberInGroup('image', 'look', 'curves');
		rememberInGroup('video', 'fx', 'other');
		expect(lastInGroups('image')).toEqual({ fx: 'vignette', look: 'curves' });
		expect(lastInGroups('video')).toEqual({ fx: 'other' });
	});

	it('survives broken storage', () => {
		store.set('potionui_editor_group_last', '{nope');
		expect(lastInGroups('image')).toEqual({});
	});

	it('shows the selected entry, else the last used one, else the first', () => {
		const entries = ['film_grain', 'vignette', 'grayscale'];
		expect(shownInGroup(entries, 'grayscale', 'vignette')).toBe('grayscale');
		expect(shownInGroup(entries, 'crop', 'vignette')).toBe('vignette');
		expect(shownInGroup(entries, null, 'gone')).toBe('film_grain');
	});
});
