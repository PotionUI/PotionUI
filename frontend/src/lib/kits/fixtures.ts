import { countByKind } from './kitCounts';
import type { Kit, KitDetail, KitItem, KitItemKind } from './types';

let counter = 0;

function hex(n: number): string {
	return n.toString(16).padStart(8, '0');
}

export function makeItem(kind: KitItemKind, name: string, extra: Partial<KitItem> = {}): KitItem {
	counter += 1;
	const id = extra.id ?? `it_${hex(counter)}`;
	return {
		id,
		kit_id: 'kt_00000001',
		kind,
		name,
		position: counter,
		media_ref: kind === 'text' ? null : `up_${id}`,
		text: kind === 'text' ? 'A note' : null,
		url: kind === 'text' ? null : `/media/${id}`,
		thumbnail_url: kind === 'image' || kind === 'video' ? `/thumb/${id}` : null,
		width: kind === 'image' ? 768 : null,
		height: kind === 'image' ? 1024 : null,
		duration_seconds: kind === 'video' || kind === 'audio' ? 6 : null,
		missing: false,
		created_at: '2026-11-12T08:00:00Z',
		...extra
	};
}

export function makeKit(name: string, handle: string, items: KitItem[], extra: Partial<KitDetail> = {}): KitDetail {
	counter += 1;
	const id = extra.id ?? `kt_${hex(counter + 4096)}`;
	const withKit = items.map((item, index) => ({ ...item, kit_id: id, position: index }));
	const summary: Kit = {
		id,
		name,
		handle,
		label: null,
		position: 0,
		item_count: withKit.length,
		counts: countByKind(withKit),
		preview_items: withKit.filter((item) => item.kind !== 'text').slice(0, 3),
		created_at: '2026-11-12T08:00:00Z',
		updated_at: '2026-11-12T08:00:00Z'
	};
	return { ...summary, items: withKit, ...extra };
}
