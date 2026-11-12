import { describe, expect, it, vi } from 'vitest';
import type { AxiosInstance } from 'axios';
import { createKitsApi, kitListParams, toKit, toKitDetail, toLabels, type WireItem, type WireKit } from './kits';

function client(method: string, data: unknown) {
	const fn = vi.fn().mockResolvedValue({ data });
	return { fn, api: createKitsApi({ [method]: fn } as unknown as AxiosInstance) };
}

function wireItem(extra: Partial<WireItem> = {}): WireItem {
	return {
		id: 'it_00000001',
		kit_id: 'kt_00000001',
		name: 'face',
		kind: 'image',
		position: 0,
		text: null,
		media: {
			upload_id: 'up_1',
			url: '/api/media/uploads/a.png',
			thumbnail_small: '/api/media/uploads/a.png?size=small',
			thumbnail_medium: '/api/media/uploads/a.png?size=medium',
			width: 768,
			height: 1024,
			duration_seconds: null
		},
		missing: false,
		created_at: '2026-11-12T08:00:00Z',
		...extra
	};
}

function wireKit(extra: Partial<WireKit> = {}): WireKit {
	return {
		id: 'kt_00000001',
		name: 'Mara',
		handle: 'mara',
		label: 'Actor',
		position: 0,
		counts: { image: 1, video: 0, audio: 0, text: 0 },
		items: [wireItem()],
		created_at: '2026-11-12T08:00:00Z',
		updated_at: '2026-11-12T08:00:00Z',
		...extra
	};
}

describe('wire mapping', () => {
	it('flattens the nested media of an item', () => {
		const detail = toKitDetail(wireKit());
		expect(detail.items[0]).toMatchObject({
			media_ref: 'up_1',
			url: '/api/media/uploads/a.png',
			thumbnail_url: '/api/media/uploads/a.png?size=medium',
			width: 768,
			missing: false
		});
		expect(detail.item_count).toBe(1);
	});

	it('keeps a deleted file as a missing item with no media', () => {
		const detail = toKitDetail(wireKit({ items: [wireItem({ media: null, missing: true })] }));
		expect(detail.items[0]).toMatchObject({ missing: true, media_ref: null, url: null });
	});

	it('keeps text inline and does not invent a picture for it', () => {
		const detail = toKitDetail(wireKit({ items: [wireItem({ kind: 'text', media: null, text: 'Hello' })] }));
		expect(detail.items[0]).toMatchObject({ kind: 'text', text: 'Hello', thumbnail_url: null });
	});

	it('builds the card mosaic from the summary thumbnails and adds a note cell for text', () => {
		const kit = toKit(
			wireKit({
				items: undefined,
				item_count: 3,
				counts: { image: 1, video: 0, audio: 1, text: 1 },
				thumbnails: [
					{ item_id: 'it_a', kind: 'image', url: '/a.png', thumbnail_small: null, thumbnail_medium: '/a-m.png' },
					{ item_id: 'it_b', kind: 'audio', url: '/b.wav', thumbnail_small: null, thumbnail_medium: null }
				]
			})
		);
		expect(kit.preview_items.map((item) => item.kind)).toEqual(['image', 'audio', 'text']);
		expect(kit.preview_items[0].thumbnail_url).toBe('/a-m.png');
		expect(kit.item_count).toBe(3);
	});

	it('reads labels as plain strings or with counts', () => {
		expect(toLabels(['Actor', 'Prop'])).toEqual([
			{ label: 'Actor', count: null },
			{ label: 'Prop', count: null }
		]);
		expect(toLabels([{ label: 'Actor', count: 2 }, '', 4])).toEqual([{ label: 'Actor', count: 2 }]);
		expect(toLabels(undefined)).toEqual([]);
	});
});

describe('kitListParams', () => {
	it('sends only what is set, as limit and offset', () => {
		expect(kitListParams({})).toEqual({});
		expect(kitListParams({ label: 'Actor', search: ' ma ', page: 3, page_size: 8 })).toEqual({
			label: 'Actor',
			search: 'ma',
			limit: 8,
			offset: 16
		});
	});

	it('leaves page 1 at offset zero', () => {
		expect(kitListParams({ page: 1, page_size: 8 })).toEqual({ limit: 8 });
	});
});

describe('createKitsApi', () => {
	it('lists kits and unwraps the page', async () => {
		const { fn, api } = client('get', { success: true, data: { kits: [wireKit({ items: undefined })], total: 9, labels: ['Actor'] } });
		const result = await api.listKits({ label: 'Actor', page: 2, page_size: 8 });
		expect(fn).toHaveBeenCalledWith('/api/kits', { params: { label: 'Actor', limit: 8, offset: 8 } });
		expect(result.data).toMatchObject({ total: 9, page: 2, page_size: 8 });
		expect(result.data?.kits[0].handle).toBe('mara');
		expect(result.data?.labels).toEqual([{ label: 'Actor', count: null }]);
	});

	it('passes a failed list through as a failure', async () => {
		const { api } = client('get', { success: false, message: 'nope' });
		expect((await api.listKits()).success).toBe(false);
	});

	it('reads one kit out of the kit envelope', async () => {
		const { fn, api } = client('get', { success: true, data: { kit: wireKit() } });
		const result = await api.getKit('kt_00000001');
		expect(fn).toHaveBeenCalledWith('/api/kits/kt_00000001');
		expect(result.data?.items).toHaveLength(1);
	});

	it('creates an empty kit with POST /api/kits', async () => {
		const { fn, api } = client('post', { success: true, data: { kit: wireKit({ items: [] }) } });
		await api.createKit({ name: 'Mara', label: 'Actor' });
		expect(fn).toHaveBeenCalledWith('/api/kits', { name: 'Mara', handle: undefined, label: 'Actor' });
	});

	it('creates a kit from Library items in one request, naming each by upload id', async () => {
		const { fn, api } = client('post', { success: true, data: { kit: wireKit() } });
		await api.createKit({
			name: 'Old harbour',
			label: 'Location',
			items: [
				{ kind: 'image', media_ref: 'up_1', name: 'lighthouse' },
				{ kind: 'audio', media_ref: 'up_2', name: 'waves' }
			]
		});
		expect(fn).toHaveBeenCalledWith('/api/kits/add-library-items', {
			upload_ids: ['up_1', 'up_2'],
			names: { up_1: 'lighthouse', up_2: 'waves' },
			new_kit: { name: 'Old harbour', label: 'Location' }
		});
	});

	it('adds text items one at a time after the media', async () => {
		const post = vi
			.fn()
			.mockResolvedValueOnce({ data: { success: true, data: { kit: wireKit() } } })
			.mockResolvedValueOnce({ data: { success: true, data: { kit: wireKit() } } });
		const api = createKitsApi({ post } as unknown as AxiosInstance);
		await api.addKitItems('kt_00000001', [
			{ kind: 'text', name: 'look', text: 'Warm' },
			{ kind: 'image', media_ref: 'up_1', name: 'face' }
		]);
		expect(post.mock.calls[0][0]).toBe('/api/kits/add-library-items');
		expect(post.mock.calls[0][1]).toMatchObject({ kit_id: 'kt_00000001', upload_ids: ['up_1'] });
		expect(post.mock.calls[1]).toEqual(['/api/kits/kt_00000001/items', { text: 'Warm', name: 'look' }]);
	});

	it('uploads a file into a kit as multipart', async () => {
		const { fn, api } = client('post', { success: true, data: { kit: wireKit() } });
		await api.uploadKitItem('kt_00000001', new File(['x'], 'a.png', { type: 'image/png' }), 'face');
		const [url, body, options] = fn.mock.calls[0];
		expect(url).toBe('/api/kits/kt_00000001/items/upload');
		expect((body as FormData).get('name')).toBe('face');
		expect(options).toEqual({ headers: { 'Content-Type': 'multipart/form-data' } });
	});

	it('patches only the fields it is given', async () => {
		const { fn, api } = client('patch', { success: true, data: { kit: wireKit() } });
		await api.updateKit('kt_00000001', { label: null });
		expect(fn).toHaveBeenCalledWith('/api/kits/kt_00000001', { label: null });
	});

	it('renames and removes items and gets the kit back each time', async () => {
		const patch = client('patch', { success: true, data: { kit: wireKit() } });
		const renamed = await patch.api.updateKitItem('kt_1', 'it_2', { name: 'profile_left' });
		expect(patch.fn).toHaveBeenCalledWith('/api/kits/kt_1/items/it_2', { name: 'profile_left' });
		expect(renamed.data?.handle).toBe('mara');
		const del = client('delete', { success: true, data: { kit: wireKit() } });
		await del.api.removeKitItem('kt_1', 'it_2');
		expect(del.fn).toHaveBeenCalledWith('/api/kits/kt_1/items/it_2');
	});

	it('reorders by the whole id list', async () => {
		const { fn, api } = client('put', { success: true, data: { kit: wireKit() } });
		await api.reorderKitItems('kt_1', ['it_2', 'it_1']);
		expect(fn).toHaveBeenCalledWith('/api/kits/kt_1/order', { ids: ['it_2', 'it_1'] });
	});

	it('duplicates and deletes', async () => {
		const dup = client('post', { success: true, data: { kit: wireKit() } });
		await dup.api.duplicateKit('kt_1');
		expect(dup.fn).toHaveBeenCalledWith('/api/kits/kt_1/duplicate');
		const del = client('delete', { success: true, data: { id: 'kt_1', deleted: true } });
		expect((await del.api.deleteKit('kt_1')).success).toBe(true);
		expect(del.fn).toHaveBeenCalledWith('/api/kits/kt_1');
	});

	it('asks which kits use some Library files', async () => {
		const { fn, api } = client('get', { success: true, data: { usage: { up_1: { count: 2, items: [] } } } });
		const result = await api.getKitUsageForUploads(['up_1', 'up_2']);
		expect(fn).toHaveBeenCalledWith('/api/kits/usage', { params: { upload_ids: 'up_1,up_2' } });
		expect(result.data?.up_1.count).toBe(2);
	});

	it('resolves citations', async () => {
		const { fn, api } = client('post', { success: true, data: { field: 'refs', items: [], skipped: [], expanded: '', text: '', takes: {}, limits: {} } });
		await api.resolveKitCitations({ text: '@kits.mara', loaded: ['kt_1'], field: 'refs' });
		expect(fn).toHaveBeenCalledWith('/api/kits/resolve', { text: '@kits.mara', loaded: ['kt_1'], field: 'refs' });
	});

	it('escapes an id before it goes into a path', async () => {
		const { fn, api } = client('get', { success: true, data: { kit: wireKit() } });
		await api.getKit('a/b');
		expect(fn).toHaveBeenCalledWith('/api/kits/a%2Fb');
	});
});
