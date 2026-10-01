import { describe, it, expect } from 'vitest';
import {
	historyMediaKey,
	isMediaTypeSelectable,
	itemsForKeys,
	mediaTypeConstraintMessage,
	pickedFromHistoryFile,
	pickedFromLibraryItem,
	sameKeys,
	toggleKey
} from './mediaPickerSelection';

describe('mediaPickerSelection', () => {
	it('builds typed keys', () => {
		expect(historyMediaKey('g', 4)).toBe('g:4');
		const picked = pickedFromHistoryFile({ id: 'g' } as any, { id: 4, file_type: 'Video/mp4', file_path: 'a/b.mp4' } as any);
		expect(picked).toMatchObject({ key: 'g:4', mediaType: 'video', filename: 'b.mp4' });
		expect(pickedFromLibraryItem({ id: 'x', filename: 's', original_filename: 'o.png', media_type: 'image' } as any)).toMatchObject({
			key: 'x',
			filename: 'o.png',
			origin: { kind: 'library', itemId: 'x' }
		});
	});

	it('applies the media type constraint', () => {
		expect(isMediaTypeSelectable('video', undefined)).toBe(true);
		expect(isMediaTypeSelectable('video', ['image'])).toBe(false);
		expect(isMediaTypeSelectable('image', ['image', 'video'])).toBe(true);
		expect(mediaTypeConstraintMessage(['image', 'video'])).toBe('Only image and video files can be selected here');
		expect(mediaTypeConstraintMessage(undefined)).toBe('');
	});

	it('toggles and compares keys, and resolves known items', () => {
		expect(toggleKey(['a'], 'b')).toEqual(['a', 'b']);
		expect(toggleKey(['a', 'b'], 'a')).toEqual(['b']);
		expect(sameKeys(['a', 'b'], ['a', 'b'])).toBe(true);
		expect(sameKeys(['a'], ['b'])).toBe(false);
		const known = new Map([['a', { key: 'a' } as any]]);
		expect(itemsForKeys(['a', 'zz:3'], known, 'history')).toEqual([
			{ key: 'a' },
			{ key: 'zz:3', mediaType: null, filename: null, origin: { kind: 'history', generationId: 'zz', fileId: 3 } }
		]);
		expect(itemsForKeys(['q'], known, 'library')[0].origin).toEqual({ kind: 'library', itemId: 'q' });
	});
});
