import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { nsfwRevealStore, revealKey } from './nsfwReveal';

describe('stores/nsfwReveal', () => {
	beforeEach(() => {
		nsfwRevealStore.reset();
	});

	it('starts empty', () => {
		expect(get(nsfwRevealStore).size).toBe(0);
	});

	it('reveal adds the key and is idempotent', () => {
		nsfwRevealStore.reveal('gen-1:5');
		nsfwRevealStore.reveal('gen-1:5');
		const revealed = get(nsfwRevealStore);
		expect(revealed.size).toBe(1);
		expect(revealed.has('gen-1:5')).toBe(true);
	});

	it('keeps distinct generations/files separate', () => {
		nsfwRevealStore.reveal('gen-1:5');
		nsfwRevealStore.reveal('gen-2:5');
		const revealed = get(nsfwRevealStore);
		expect(revealed.has('gen-1:5')).toBe(true);
		expect(revealed.has('gen-2:5')).toBe(true);
		expect(revealed.size).toBe(2);
	});

	it('reset clears all reveals', () => {
		nsfwRevealStore.reveal('gen-1:5');
		nsfwRevealStore.reset();
		expect(get(nsfwRevealStore).size).toBe(0);
	});
});

describe('revealKey', () => {
	it('keys by generation id + file id when an id is present', () => {
		expect(revealKey('gen-1', { id: 5, file_path: '/a/b.png' })).toBe('gen-1:5');
	});

	it('falls back to file_path when there is no id', () => {
		expect(revealKey('gen-1', { file_path: '/a/b.png' })).toBe('gen-1:/a/b.png');
	});
});
