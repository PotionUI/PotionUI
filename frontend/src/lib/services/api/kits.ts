import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';
import type {
	Kit,
	KitDetail,
	KitItem,
	KitItemKind,
	KitItemPatch,
	KitLabelCount,
	KitListPage,
	KitListQuery,
	KitPatch,
	KitResolution,
	KitResolveRequest,
	KitUploadUsage,
	KitUsage,
	NewKit,
	NewKitItem
} from '$lib/kits/types';
import { countByKind } from '$lib/kits/kitCounts';

const seg = encodeURIComponent;

export interface WireMedia {
	upload_id: string;
	url: string | null;
	thumbnail_small: string | null;
	thumbnail_medium: string | null;
	width: number | null;
	height: number | null;
	duration_seconds: number | null;
}

export interface WireItem {
	id: string;
	kit_id: string;
	name: string;
	kind: KitItemKind;
	position: number;
	text: string | null;
	media: WireMedia | null;
	missing: boolean;
	created_at: string;
}

export interface WireThumbnail {
	item_id: string;
	kind: KitItemKind;
	url: string | null;
	thumbnail_small: string | null;
	thumbnail_medium: string | null;
}

export interface WireKit {
	id: string;
	name: string;
	handle: string;
	label: string | null;
	position: number;
	counts?: Partial<Record<KitItemKind, number>>;
	item_count?: number;
	items?: WireItem[];
	thumbnails?: WireThumbnail[];
	created_at: string;
	updated_at: string;
}

export function toKitItem(wire: WireItem): KitItem {
	const media = wire.media;
	const thumb = media ? (media.thumbnail_medium ?? media.thumbnail_small ?? (wire.kind === 'image' ? media.url : null)) : null;
	return {
		id: wire.id,
		kit_id: wire.kit_id,
		kind: wire.kind,
		name: wire.name,
		position: wire.position,
		media_ref: media?.upload_id ?? null,
		text: wire.kind === 'text' ? (wire.text ?? '') : null,
		url: media?.url ?? null,
		thumbnail_url: thumb,
		width: media?.width ?? null,
		height: media?.height ?? null,
		duration_seconds: media?.duration_seconds ?? null,
		missing: !!wire.missing,
		created_at: wire.created_at
	};
}

function previewFrom(wire: WireKit, kitId: string): KitItem[] {
	if (wire.items && wire.items.length > 0) return wire.items.map(toKitItem).slice(0, 4);
	const stubs: KitItem[] = (wire.thumbnails ?? []).map((thumb, index) => ({
		id: thumb.item_id,
		kit_id: kitId,
		kind: thumb.kind,
		name: '',
		position: index,
		media_ref: null,
		text: null,
		url: thumb.url,
		thumbnail_url: thumb.thumbnail_medium ?? thumb.thumbnail_small ?? (thumb.kind === 'image' ? thumb.url : null),
		width: null,
		height: null,
		duration_seconds: null,
		missing: false,
		created_at: ''
	}));
	const texts = wire.counts?.text ?? 0;
	if (texts > 0 && stubs.length < 3) {
		stubs.push({
			id: `${kitId}-text`,
			kit_id: kitId,
			kind: 'text',
			name: '',
			position: stubs.length,
			media_ref: null,
			text: '',
			url: null,
			thumbnail_url: null,
			width: null,
			height: null,
			duration_seconds: null,
			missing: false,
			created_at: ''
		});
	}
	return stubs;
}

export function toKit(wire: WireKit): Kit {
	const items = wire.items?.map(toKitItem);
	const counts = wire.counts ?? (items ? countByKind(items) : {});
	const total = wire.item_count ?? items?.length ?? Object.values(counts).reduce((sum, value) => sum + (value ?? 0), 0);
	return {
		id: wire.id,
		name: wire.name,
		handle: wire.handle,
		label: wire.label ?? null,
		position: wire.position ?? 0,
		item_count: total,
		counts,
		preview_items: previewFrom(wire, wire.id),
		created_at: wire.created_at,
		updated_at: wire.updated_at
	};
}

export function toKitDetail(wire: WireKit): KitDetail {
	return { ...toKit(wire), items: (wire.items ?? []).map(toKitItem) };
}

export function toLabels(raw: unknown): KitLabelCount[] {
	if (!Array.isArray(raw)) return [];
	return raw
		.map((entry): KitLabelCount | null => {
			if (typeof entry === 'string') return entry ? { label: entry, count: null } : null;
			if (entry && typeof entry === 'object' && typeof (entry as { label?: unknown }).label === 'string') {
				const count = (entry as { count?: unknown }).count;
				return { label: (entry as { label: string }).label, count: typeof count === 'number' ? count : null };
			}
			return null;
		})
		.filter((entry): entry is KitLabelCount => entry !== null);
}

export function kitListParams(query: KitListQuery): Record<string, string | number> {
	const params: Record<string, string | number> = {};
	if (query.label) params.label = query.label;
	if (query.search?.trim()) params.search = query.search.trim();
	const size = query.page_size;
	if (size) params.limit = size;
	if (size && query.page && query.page > 1) params.offset = (query.page - 1) * size;
	return params;
}

function fail<T>(response: APIResponse<unknown>): APIResponse<T> {
	return { success: false, message: response.message, error: response.error };
}

function detailOf(response: APIResponse<{ kit?: WireKit }>): APIResponse<KitDetail> {
	if (!response.success || !response.data?.kit) return fail(response);
	return { success: true, data: toKitDetail(response.data.kit) };
}

export function createKitsApi(client: AxiosInstance) {
	async function addText(kitId: string, text: string, name?: string): Promise<APIResponse<KitDetail>> {
		const response = await client.post(`/api/kits/${seg(kitId)}/items`, { text, ...(name ? { name } : {}) });
		return detailOf(response.data);
	}

	async function addMedia(
		items: NewKitItem[],
		target: { kit_id?: string; new_kit?: { name: string; label?: string | null } }
	): Promise<APIResponse<KitDetail>> {
		const names: Record<string, string> = {};
		for (const item of items) if (item.media_ref && item.name) names[item.media_ref] = item.name;
		const response = await client.post('/api/kits/add-library-items', {
			upload_ids: items.map((item) => item.media_ref),
			names,
			...target
		});
		return detailOf(response.data);
	}

	return {
		async listKits(query: KitListQuery = {}): Promise<APIResponse<KitListPage>> {
			const response = await client.get('/api/kits', { params: kitListParams(query) });
			const body = response.data as APIResponse<{ kits: WireKit[]; total: number; labels: unknown }>;
			if (!body.success || !body.data) return fail(body);
			return {
				success: true,
				data: {
					kits: (body.data.kits ?? []).map(toKit),
					total: body.data.total ?? 0,
					page: query.page ?? 1,
					page_size: query.page_size ?? 50,
					labels: toLabels(body.data.labels)
				}
			};
		},

		async getKit(id: string): Promise<APIResponse<KitDetail>> {
			const response = await client.get(`/api/kits/${seg(id)}`);
			return detailOf(response.data);
		},

		async createKit(body: NewKit): Promise<APIResponse<KitDetail>> {
			const items = body.items ?? [];
			const media = items.filter((item) => item.kind !== 'text' && item.media_ref);
			const texts = items.filter((item) => item.kind === 'text');
			let created: APIResponse<KitDetail>;
			if (media.length > 0) {
				created = await addMedia(media, { new_kit: { name: body.name, label: body.label ?? null } });
			} else {
				const response = await client.post('/api/kits', { name: body.name, handle: body.handle, label: body.label ?? null });
				created = detailOf(response.data);
			}
			if (!created.success || !created.data) return created;
			let latest = created;
			for (const item of texts) latest = await addText(created.data.id, item.text ?? '', item.name);
			return latest;
		},

		async updateKit(id: string, patch: KitPatch): Promise<APIResponse<KitDetail>> {
			const response = await client.patch(`/api/kits/${seg(id)}`, patch);
			return detailOf(response.data);
		},

		async deleteKit(id: string): Promise<APIResponse<{ id: string }>> {
			const response = await client.delete(`/api/kits/${seg(id)}`);
			return response.data;
		},

		async getKitUsage(id: string): Promise<APIResponse<KitUsage>> {
			const response = await client.get(`/api/kits/${seg(id)}/usage`);
			return response.data;
		},

		async duplicateKit(id: string): Promise<APIResponse<KitDetail>> {
			const response = await client.post(`/api/kits/${seg(id)}/duplicate`);
			return detailOf(response.data);
		},

		async addKitItems(id: string, items: NewKitItem[]): Promise<APIResponse<KitDetail>> {
			const media = items.filter((item) => item.kind !== 'text' && item.media_ref);
			let latest: APIResponse<KitDetail> | null = null;
			if (media.length > 0) latest = await addMedia(media, { kit_id: id });
			if (latest && !latest.success) return latest;
			for (const item of items.filter((entry) => entry.kind === 'text')) {
				latest = await addText(id, item.text ?? '', item.name);
				if (!latest.success) return latest;
			}
			return latest ?? { success: false, message: 'Nothing to add' };
		},

		async uploadKitItem(id: string, file: File, name?: string): Promise<APIResponse<KitDetail>> {
			const form = new FormData();
			form.append('file', file);
			if (name) form.append('name', name);
			const response = await client.post(`/api/kits/${seg(id)}/items/upload`, form, {
				headers: { 'Content-Type': 'multipart/form-data' }
			});
			return detailOf(response.data);
		},

		async updateKitItem(id: string, itemId: string, patch: KitItemPatch): Promise<APIResponse<KitDetail>> {
			const response = await client.patch(`/api/kits/${seg(id)}/items/${seg(itemId)}`, patch);
			return detailOf(response.data);
		},

		async removeKitItem(id: string, itemId: string): Promise<APIResponse<KitDetail>> {
			const response = await client.delete(`/api/kits/${seg(id)}/items/${seg(itemId)}`);
			return detailOf(response.data);
		},

		async reorderKitItems(id: string, itemIds: string[]): Promise<APIResponse<KitDetail>> {
			const response = await client.put(`/api/kits/${seg(id)}/order`, { ids: itemIds });
			return detailOf(response.data);
		},

		async getKitUsageForUploads(uploadIds: string[]): Promise<APIResponse<Record<string, KitUploadUsage>>> {
			const response = await client.get('/api/kits/usage', { params: { upload_ids: uploadIds.join(',') } });
			const body = response.data as APIResponse<{ usage: Record<string, KitUploadUsage> }>;
			if (!body.success || !body.data) return fail(body);
			return { success: true, data: body.data.usage ?? {} };
		},

		async resolveKitCitations(request: KitResolveRequest): Promise<APIResponse<KitResolution>> {
			const response = await client.post('/api/kits/resolve', request);
			return response.data;
		}
	};
}

export type KitsApi = ReturnType<typeof createKitsApi>;
