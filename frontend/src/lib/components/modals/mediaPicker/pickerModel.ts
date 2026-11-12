import { buildLibraryQuery, DEFAULT_LIBRARY_FILTERS, type LibraryQuery } from '$lib/library/libraryQuery';
import type { KitListQuery } from '$lib/kits/types';
import type { SortBy, SortDir } from '$lib/types/history';
import { buildHistoryFilterParams } from '../generationHistoryFilterParams';
import type { PickerMediaType } from '../mediaPickerSelection';

export type PickerSourceId = 'history' | 'library' | 'kits';

export const SOURCE_META: Record<PickerSourceId, { label: string; icon: string; noun: string }> = {
	history: { label: 'History', icon: 'clock', noun: 'generations' },
	library: { label: 'Library', icon: 'photo', noun: 'items' },
	kits: { label: 'Kits', icon: 'layers', noun: 'kits' }
};

export const PAGE_SIZES = [10, 20, 50, 100] as const;
export const DEFAULT_PAGE_SIZE = 20;
export const KIT_PAGE_SIZE = 20;

export const KIND_LABELS: Record<PickerMediaType, string> = {
	image: 'Images',
	video: 'Videos',
	audio: 'Audio',
	mesh: '3D'
};

export type KindFilter = 'all' | PickerMediaType;
export type DatePreset = 'all' | 'today' | 'yesterday' | 'last_week' | 'last_month' | 'custom';
export type HistorySort = 'newest' | 'oldest' | 'rated' | 'largest';

export const DATE_PRESETS: ReadonlyArray<{ id: Exclude<DatePreset, 'custom'>; label: string }> = [
	{ id: 'all', label: 'All' },
	{ id: 'today', label: 'Today' },
	{ id: 'yesterday', label: 'Yesterday' },
	{ id: 'last_week', label: '7 Days' },
	{ id: 'last_month', label: '30 Days' }
];

export const SORT_OPTIONS: ReadonlyArray<{ id: HistorySort; label: string; by: SortBy; dir: SortDir }> = [
	{ id: 'newest', label: 'Newest', by: 'created_at', dir: 'desc' },
	{ id: 'oldest', label: 'Oldest', by: 'created_at', dir: 'asc' },
	{ id: 'rated', label: 'Highest rated', by: 'rating', dir: 'desc' },
	{ id: 'largest', label: 'Largest first', by: 'file_size', dir: 'desc' }
];

export interface HistoryFilters {
	search: string;
	kind: KindFilter;
	favorites: boolean;
	collectionId: string | null;
	tagIds: string[];
	date: DatePreset;
	dateFrom: string;
	dateTo: string;
	model: string;
	presetId: string;
	sort: HistorySort;
}

export interface LibraryPickFilters {
	search: string;
	kind: KindFilter;
	favorites: boolean;
	collectionId: string | null;
	unsorted: boolean;
	tagIds: string[];
}

export interface KitFilters {
	search: string;
	label: string | null;
}

export interface PickerFilters {
	history: HistoryFilters;
	library: LibraryPickFilters;
	kits: KitFilters;
}

export function freshHistoryFilters(): HistoryFilters {
	return {
		search: '',
		kind: 'all',
		favorites: false,
		collectionId: null,
		tagIds: [],
		date: 'all',
		dateFrom: '',
		dateTo: '',
		model: '',
		presetId: '',
		sort: 'newest'
	};
}

export function freshLibraryFilters(): LibraryPickFilters {
	return { search: '', kind: 'all', favorites: false, collectionId: null, unsorted: false, tagIds: [] };
}

export function freshKitFilters(): KitFilters {
	return { search: '', label: null };
}

export function freshFilters(): PickerFilters {
	return { history: freshHistoryFilters(), library: freshLibraryFilters(), kits: freshKitFilters() };
}

export type PickerTier = 'xl' | 'md' | 'sm';

export function tierForWidth(width: number): PickerTier {
	if (width >= 1280) return 'xl';
	if (width >= 768) return 'md';
	return 'sm';
}

function pad(value: number): string {
	return String(value).padStart(2, '0');
}

function isoDay(date: Date): string {
	return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function datePresetRange(preset: DatePreset, now: Date = new Date()): { from: string | null; to: string | null } {
	const today = new Date(now);
	today.setHours(0, 0, 0, 0);
	if (preset === 'today') return { from: isoDay(today), to: isoDay(today) };
	if (preset === 'yesterday') {
		const day = new Date(today);
		day.setDate(day.getDate() - 1);
		return { from: isoDay(day), to: isoDay(day) };
	}
	if (preset === 'last_week') {
		const from = new Date(today);
		from.setDate(from.getDate() - 7);
		return { from: isoDay(from), to: isoDay(today) };
	}
	if (preset === 'last_month') {
		const from = new Date(today);
		from.setMonth(from.getMonth() - 1);
		return { from: isoDay(from), to: isoDay(today) };
	}
	return { from: null, to: null };
}

export function effectiveKind(selected: KindFilter, kinds: readonly PickerMediaType[]): PickerMediaType | null {
	if (selected !== 'all') return selected;
	return kinds.length === 1 ? kinds[0] : null;
}

export interface HistoryRequest {
	limit: number;
	offset: number;
	status: string;
	includeTags: boolean;
	mediaType?: PickerMediaType;
	createdFrom?: string;
	createdTo?: string;
	search?: string;
	favoritesOnly?: boolean;
	presetId?: string;
	modelName?: string;
	tagIds?: string[];
	collectionId?: string;
	sortBy?: SortBy;
	sortDir?: SortDir;
}

export function toHistoryRequest(
	filters: HistoryFilters,
	context: { kinds: readonly PickerMediaType[]; page: number; pageSize: number; now?: Date }
): HistoryRequest {
	const request: HistoryRequest = {
		limit: context.pageSize,
		offset: (Math.max(1, context.page) - 1) * context.pageSize,
		status: 'completed',
		includeTags: true
	};
	const kind = effectiveKind(filters.kind, context.kinds);
	if (kind) request.mediaType = kind;
	const range =
		filters.date === 'custom'
			? { from: filters.dateFrom || null, to: filters.dateTo || null }
			: datePresetRange(filters.date, context.now);
	if (range.from) request.createdFrom = range.from;
	if (range.to) request.createdTo = range.to;
	const search = filters.search.trim();
	if (search) request.search = search;
	if (filters.favorites) request.favoritesOnly = true;
	if (filters.presetId) request.presetId = filters.presetId;
	if (filters.model) request.modelName = filters.model;
	Object.assign(request, buildHistoryFilterParams({ tagIds: filters.tagIds, collectionId: filters.collectionId }));
	const sort = SORT_OPTIONS.find((option) => option.id === filters.sort);
	if (sort && sort.id !== 'newest') {
		request.sortBy = sort.by;
		request.sortDir = sort.dir;
	}
	return request;
}

export function toLibraryRequest(
	filters: LibraryPickFilters,
	context: { kinds: readonly PickerMediaType[]; page: number; pageSize: number }
): LibraryQuery {
	const kind = effectiveKind(filters.kind, context.kinds);
	const mediaType = kind === 'image' || kind === 'video' || kind === 'audio' ? kind : 'all';
	return buildLibraryQuery(
		{
			...DEFAULT_LIBRARY_FILTERS,
			mediaType,
			selectedTagIds: filters.tagIds,
			collectionId: filters.collectionId ?? undefined,
			unsorted: filters.unsorted,
			favoritesOnly: filters.favorites,
			search: filters.search
		},
		context.page,
		context.pageSize
	);
}

export function toKitRequest(filters: KitFilters, page: number): KitListQuery {
	return { search: filters.search.trim(), label: filters.label, page, page_size: KIT_PAGE_SIZE };
}

export function activeFilterCount(source: PickerSourceId, filters: PickerFilters, kinds: readonly PickerMediaType[]): number {
	if (source === 'kits') {
		const f = filters.kits;
		return (f.search.trim() ? 1 : 0) + (f.label ? 1 : 0);
	}
	const showsKind = kinds.length > 1;
	if (source === 'library') {
		const f = filters.library;
		return (
			(f.search.trim() ? 1 : 0) +
			(showsKind && f.kind !== 'all' ? 1 : 0) +
			(f.favorites ? 1 : 0) +
			(f.collectionId || f.unsorted ? 1 : 0) +
			f.tagIds.length
		);
	}
	const f = filters.history;
	return (
		(f.search.trim() ? 1 : 0) +
		(showsKind && f.kind !== 'all' ? 1 : 0) +
		(f.favorites ? 1 : 0) +
		(f.collectionId ? 1 : 0) +
		f.tagIds.length +
		(f.date !== 'all' ? 1 : 0) +
		(f.model ? 1 : 0) +
		(f.presetId ? 1 : 0)
	);
}

export function popoverFilterCount(
	source: PickerSourceId,
	filters: PickerFilters,
	tier: PickerTier,
	kinds: readonly PickerMediaType[]
): number {
	if (source === 'kits') return tier === 'sm' && filters.kits.label ? 1 : 0;
	const f = source === 'history' ? filters.history : filters.library;
	let count = 0;
	if (source === 'history') {
		const h = filters.history;
		count += (h.date !== 'all' ? 1 : 0) + (h.model ? 1 : 0) + (h.presetId ? 1 : 0);
	}
	if (tier !== 'xl') {
		const collection = source === 'library' ? (filters.library.collectionId || filters.library.unsorted ? 1 : 0) : filters.history.collectionId ? 1 : 0;
		count += collection + f.tagIds.length;
	}
	if (tier === 'sm') {
		count += (f.kind !== 'all' && kinds.length > 1 ? 1 : 0) + (f.favorites ? 1 : 0);
		if (source === 'history' && filters.history.sort !== 'newest') count += 1;
	}
	return count;
}

export function hasFiltersButton(source: PickerSourceId, tier: PickerTier): boolean {
	if (source === 'history') return true;
	if (source === 'library') return tier !== 'xl';
	return tier === 'sm';
}

export function clearSource(source: PickerSourceId, filters: PickerFilters): PickerFilters {
	if (source === 'history') return { ...filters, history: freshHistoryFilters() };
	if (source === 'library') return { ...filters, library: freshLibraryFilters() };
	return { ...filters, kits: freshKitFilters() };
}

export function pagesFor(total: number, pageSize: number): number {
	return Math.max(1, Math.ceil(Math.max(0, total) / Math.max(1, pageSize)));
}

export function rangeText(page: number, pageSize: number, total: number): string {
	if (total <= 0) return '';
	const from = (page - 1) * pageSize + 1;
	const to = Math.min(page * pageSize, total);
	return `Showing ${from}–${to} of ${total}`;
}
