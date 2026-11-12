import { describe, expect, it } from 'vitest';
import type { LibraryItem } from '$lib/services/api/library';
import { describeLimit, kindOfMediaType, pickFromLibraryItem, picksFromLibraryItems, picksNames, toNewItems } from './kitPicks';

function library(id: string, media_type: string, extra: Partial<LibraryItem> = {}): LibraryItem {
	return { id, filename: `${id}.bin`, media_type, url: `/u/${id}`, tags: [], ...extra };
}

describe('kindOfMediaType', () => {
	it('knows the three media kinds and nothing else', () => {
		expect(kindOfMediaType('Image')).toBe('image');
		expect(kindOfMediaType('mesh')).toBeNull();
		expect(kindOfMediaType(undefined)).toBeNull();
	});
});

describe('picks from the Library', () => {
	it('takes the original file name as the label and the item id as the reference', () => {
		const pick = pickFromLibraryItem(library('u1', 'image', { original_filename: 'Mara, rain.png' }));
		expect(pick).toMatchObject({ kind: 'image', label: 'Mara, rain.png', mediaRef: 'u1', thumbnail: '/u/u1' });
	});

	it('leaves out what a kit cannot hold', () => {
		expect(picksFromLibraryItems([library('u1', 'image'), library('u2', 'mesh')])).toHaveLength(1);
	});

	it('does not use a clip as its own thumbnail', () => {
		expect(pickFromLibraryItem(library('u3', 'video'))?.thumbnail).toBeNull();
	});
});

describe('names and request items', () => {
	const picks = picksFromLibraryItems([library('u1', 'image'), library('u2', 'image'), library('u3', 'video'), library('u4', 'audio')]);

	it('numbers per kind and avoids names already in the kit', () => {
		expect(picksNames(picks)).toEqual(['image1', 'image2', 'video1', 'audio1']);
		expect(picksNames(picks, ['image1'])).toEqual(['image2', 'image3', 'video1', 'audio1']);
	});

	it('sends the reference, not the file', () => {
		const items = toNewItems(picks, ['a', 'b', 'c', 'd']);
		expect(items[0]).toEqual({ kind: 'image', name: 'a', media_ref: 'u1' });
	});

	it('sends text inline', () => {
		const items = toNewItems([{ key: 't', kind: 'text', label: 'Note', thumbnail: null, mediaRef: null, text: 'hello' }], ['text1']);
		expect(items).toEqual([{ kind: 'text', name: 'text1', text: 'hello' }]);
	});
});

describe('describeLimit', () => {
	it('is quiet while there is room', () => {
		expect(describeLimit(10, 5, 50)).toBeNull();
		expect(describeLimit(45, 5, 50)).toBeNull();
	});

	it('says how much room is left', () => {
		expect(describeLimit(48, 5, 50)).toBe('A kit holds 50 items. There is room for 2 more.');
		expect(describeLimit(50, 1, 50)).toBe('A kit holds 50 items and this one is full.');
	});
});
