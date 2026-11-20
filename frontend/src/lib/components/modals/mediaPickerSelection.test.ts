import { describe, it, expect } from 'vitest';
import {
	historyMediaKey,
	historyTileLabel,
	promptExcerptOf,
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

describe('historyTileLabel', () => {
	const file = { id: 1, file_path: 'outputs/2026-10-08/01K/0.png' } as any;

	it('tells tiles apart by prompt, preset and date instead of the bare file name', () => {
		const generation = {
			id: 'g',
			preset_name: 'FLUX 3',
			created_at: '2026-10-08T10:00:00Z',
			form_data: { prompt: '  a candlelit   potion shop at night, rain on the window, volumetric light  ' }
		} as any;
		const tile = historyTileLabel(generation, file);
		expect(tile.label.startsWith('a candlelit potion shop at night')).toBe(true);
		expect(tile.label.endsWith('…')).toBe(true);
		expect(tile.label).not.toContain('0.png');
		expect(tile.sublabel).toBe('FLUX 3 · 2026-10-08');
	});

	it('falls back to the preset, then the file name', () => {
		const base = { id: 'g', created_at: '2026-10-08T10:00:00Z', form_data: {} } as any;
		expect(historyTileLabel({ ...base, preset_name: 'FLUX 3' }, file)).toEqual({ label: 'FLUX 3', sublabel: '2026-10-08' });
		expect(historyTileLabel(base, file).label).toBe('0.png');
	});
});

describe('promptExcerptOf', () => {
	it('cuts a long prompt to a short single line', () => {
		const text = promptExcerptOf({ prompt: 'a lighthouse\non a cliff at dusk, volumetric light, film grain, 35mm' });
		expect(text.length).toBeLessThanOrEqual(40);
		expect(text.startsWith('a lighthouse on a cliff')).toBe(true);
	});

	it('is empty without a prompt', () => {
		expect(promptExcerptOf({ steps: 20 })).toBe('');
		expect(promptExcerptOf(null)).toBe('');
	});
});
