export type KitItemKind = 'image' | 'video' | 'audio' | 'text';

export const KIT_ITEM_KINDS: readonly KitItemKind[] = ['image', 'video', 'audio', 'text'];

export const KIT_ITEM_LIMIT = 50;
export const KIT_TEXT_LIMIT = 4000;

export interface KitItem {
	id: string;
	kit_id: string;
	kind: KitItemKind;
	name: string;
	position: number;
	media_ref: string | null;
	text: string | null;
	url: string | null;
	thumbnail_url: string | null;
	width: number | null;
	height: number | null;
	duration_seconds: number | null;
	missing: boolean;
	created_at: string;
}

export type KitCounts = Partial<Record<KitItemKind, number>>;

export interface Kit {
	id: string;
	name: string;
	handle: string;
	label: string | null;
	position: number;
	item_count: number;
	counts: KitCounts;
	preview_items: KitItem[];
	created_at: string;
	updated_at: string;
}

export interface KitDetail extends Kit {
	items: KitItem[];
}

export interface KitLabelCount {
	label: string;
	count: number | null;
}

export interface KitListPage {
	kits: Kit[];
	total: number;
	page: number;
	page_size: number;
	labels: KitLabelCount[];
}

export interface KitListQuery {
	label?: string | null;
	search?: string;
	page?: number;
	page_size?: number;
}

export interface KitUsage {
	sessions: number;
	films: number;
}

export interface NewKitItem {
	kind: KitItemKind;
	name?: string;
	media_ref?: string;
	text?: string;
}

export interface NewKit {
	name: string;
	label?: string | null;
	items?: NewKitItem[];
}

export interface KitPatch {
	name?: string;
	handle?: string;
	label?: string | null;
}

export interface KitItemPatch {
	name?: string;
	text?: string;
}

export interface KitNotice {
	code: string;
	message: string;
	kit: string | null;
	item: string | null;
	kind: string | null;
	field: string | null;
}

export interface KitResolvedItem {
	kit: string;
	kit_name: string;
	handle: string;
	item: string;
	name: string;
	kind: KitItemKind;
	field: string | null;
	relative_path: string | null;
}

export interface KitResolveRequest {
	text: string;
	cites?: string[];
	loaded?: string[];
	field?: string | null;
	preset_id?: string | null;
	mode?: string | null;
	form_data?: Record<string, unknown[]>;
}

export interface KitResolution {
	field: string;
	items: KitResolvedItem[];
	skipped: KitNotice[];
	expanded: string;
	text: string;
	takes: Record<KitItemKind, boolean>;
	limits: Record<string, number>;
}

export interface KitUploadUsage {
	count: number;
	items: { kit_id: string; kit_name: string; handle: string; item_id: string; item_name: string }[];
}

export interface KitRowSnapshot {
	name: string;
	handle: string;
	items: { id: string; name: string }[];
}

export interface KitRow {
	kit: string;
	snapshot?: KitRowSnapshot;
}

export interface KitCreatePartial {
	created: KitDetail;
	pending: NewKitItem[];
	message: string;
}
