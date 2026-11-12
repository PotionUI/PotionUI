import { describe, expect, it, vi } from 'vitest';
import type { KitItem } from '$lib/kits/types';
import type { PickedMedia } from '../mediaPickerSelection';
import { libraryItemFromKitItem, libraryItemOf, routePicked } from './pickedMedia';
import { clockText } from './pickerEntries';

vi.mock('$lib/services/api/index', () => ({ api: { getGenerationThumbnailURL: () => '/thumb' } }));

function kitItem(extra: Partial<KitItem> = {}): KitItem {
	return {
		id: 'ki1',
		kit_id: 'kt_1',
		kind: 'image',
		name: 'face',
		position: 0,
		media_ref: 'up_7',
		text: null,
		url: '/api/media/uploads/a1b2c3.png?v=2',
		thumbnail_url: '/thumb/a1b2c3.png',
		width: 768,
		height: 1024,
		duration_seconds: null,
		missing: false,
		created_at: '',
		...extra
	};
}

describe('a kit item as a library item', () => {
	it('keeps the upload id, the stored file name and the display name', () => {
		expect(libraryItemFromKitItem(kitItem())).toMatchObject({
			id: 'up_7',
			filename: 'a1b2c3.png',
			original_filename: 'face',
			media_type: 'image',
			url: '/api/media/uploads/a1b2c3.png?v=2',
			width: 768,
			height: 1024
		});
	});

	it('falls back to the item id and name when the kit item has no upload', () => {
		expect(libraryItemFromKitItem(kitItem({ media_ref: null, url: null }))).toMatchObject({ id: 'ki1', filename: 'face', url: '' });
	});
});

describe('routing a pick', () => {
	const base = { key: 'k', mediaType: 'image', filename: 'x', origin: { kind: 'library' as const, itemId: 'l1' } };

	it('sends a History file to the history handler with the whole generation', () => {
		const handlers = { history: vi.fn(), library: vi.fn() };
		const picked = { ...base, generation: { id: 'g' }, file: { id: 1 } } as unknown as PickedMedia;
		routePicked(picked, handlers);
		expect(handlers.history).toHaveBeenCalledWith(picked.generation, picked.file);
		expect(handlers.library).not.toHaveBeenCalled();
	});

	it('sends a library item and a kit item to the library handler', () => {
		const handlers = { history: vi.fn(), library: vi.fn() };
		routePicked({ ...base, item: { id: 'l1' } } as unknown as PickedMedia, handlers);
		routePicked({ ...base, origin: { kind: 'kit', kitId: 'kt_1', itemId: 'ki1' }, kitItem: kitItem() } as unknown as PickedMedia, handlers);
		expect(handlers.library).toHaveBeenCalledTimes(2);
		expect(handlers.library.mock.calls[1][0]).toMatchObject({ id: 'up_7' });
		expect(handlers.history).not.toHaveBeenCalled();
	});

	it('ignores a text kit item: there is no media to hand over', () => {
		const handlers = { history: vi.fn(), library: vi.fn() };
		const picked = { ...base, kitItem: kitItem({ kind: 'text', media_ref: null, url: null }) } as unknown as PickedMedia;
		expect(libraryItemOf(picked)).toBeNull();
		routePicked(picked, handlers);
		expect(handlers.library).not.toHaveBeenCalled();
	});
});

describe('clock text', () => {
	it('prints m:ss and h:mm:ss', () => {
		expect(clockText(6)).toBe('0:06');
		expect(clockText(12.4)).toBe('0:12');
		expect(clockText(75)).toBe('1:15');
		expect(clockText(3725)).toBe('1:02:05');
		expect(clockText(-3)).toBe('0:00');
	});
});
