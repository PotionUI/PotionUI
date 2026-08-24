import { describe, it, expect } from 'vitest';
import { pickActiveGeneration, needsDetailFetch } from './generationDetail';
import type { GenerationHistoryItem } from '$lib/types/history';

function gen(id: string, extra: Partial<GenerationHistoryItem> = {}): GenerationHistoryItem {
	return {
		id,
		form_data: {},
		status: 'completed',
		progress: 1,
		created_at: '',
		updated_at: '',
		files: [],
		rating: 0,
		is_favorite: false,
		...extra
	} as GenerationHistoryItem;
}

describe('pickActiveGeneration', () => {
	it('prefers the loaded detail once it matches the active id', () => {
		const listItem = gen('a');
		const detail = gen('a', { segments: [] });
		expect(pickActiveGeneration(listItem, detail, 'a')).toBe(detail);
	});

	it('ignores a stale loaded detail from a previously viewed generation', () => {
		const listItem = gen('b');
		const staleDetail = gen('a', { segments: [] });
		expect(pickActiveGeneration(listItem, staleDetail, 'b')).toBe(listItem);
	});

	it('falls back to the list item before anything has loaded', () => {
		const listItem = gen('a');
		expect(pickActiveGeneration(listItem, null, 'a')).toBe(listItem);
	});

	it('returns null when the modal was handed only an id', () => {
		expect(pickActiveGeneration(null, null, 'a')).toBeNull();
	});
});

describe('needsDetailFetch', () => {
	it('fetches for a list item, which never carries segments', () => {
		expect(needsDetailFetch(true, 'a', gen('a'), undefined)).toBe(true);
	});

	it('does not fetch once segments are present', () => {
		expect(needsDetailFetch(true, 'a', gen('a', { segments: [] }), 'a')).toBe(false);
	});

	it('treats an empty segments array as loaded, so old rows settle after one fetch', () => {
		// Rows predating migration 065 have no segments; the detail endpoint
		// returns []. Without this, the reactive statement would fetch forever.
		expect(needsDetailFetch(true, 'a', gen('a', { segments: [] }), undefined)).toBe(false);
	});

	it('does not re-fire while a request for the same id is in flight', () => {
		expect(needsDetailFetch(true, 'a', gen('a'), 'a')).toBe(false);
	});

	it('fetches again after navigating to a different generation', () => {
		expect(needsDetailFetch(true, 'b', gen('b'), 'a')).toBe(true);
	});

	it('fetches when handed only an id (no generation object yet)', () => {
		expect(needsDetailFetch(true, 'a', null, undefined)).toBe(true);
	});

	it('does nothing while closed or without an id', () => {
		expect(needsDetailFetch(false, 'a', gen('a'), undefined)).toBe(false);
		expect(needsDetailFetch(true, '', null, undefined)).toBe(false);
	});
});
