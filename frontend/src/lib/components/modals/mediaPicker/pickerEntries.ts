import { api } from '$lib/services/api/index';
import type { LibraryItem } from '$lib/services/api/library';
import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';
import type { Kit, KitItem } from '$lib/kits/types';
import { libraryItemDisplayName, libraryItemGridSrc } from '$lib/library/libraryItemMeta';
import { clampAspect } from '$lib/utils/justifiedLayout';
import { formatBytes } from '$lib/utils/format';
import {
	historyTileLabel,
	pickedFromHistoryFile,
	pickedFromKitItem,
	pickedFromLibraryItem,
	type PickedMedia,
	type PickerMediaType
} from '../mediaPickerSelection';
import { filterFilesByMediaType } from '../generationHistoryMediaFilter';
import { kitItemKey } from './pickerSelection';

export type EntryKind = PickerMediaType | 'text';

export interface PickerEntry {
	key: string;
	kind: EntryKind;
	label: string;
	accessibleName: string;
	sublabel: string;
	ratio: number;
	thumb: string | null;
	duration: string | null;
	text: string | null;
	picked: PickedMedia;
}

const DEFAULT_RATIO = 1.33;

export function ratioOf(width?: number | null, height?: number | null): number {
	if (typeof width === 'number' && typeof height === 'number' && width > 0 && height > 0) {
		return clampAspect(width / height);
	}
	return DEFAULT_RATIO;
}

function isBrowserUrl(value: string | undefined | null): value is string {
	if (!value) return false;
	return /^(https?:|data:|blob:)/i.test(value) || value.startsWith('/api/') || value.startsWith('/frontend-kit/');
}

function kindOf(raw: string | null | undefined): EntryKind {
	const type = (raw ?? '').toLowerCase();
	if (type.startsWith('video')) return 'video';
	if (type.startsWith('audio')) return 'audio';
	if (type.startsWith('mesh')) return 'mesh';
	if (type.startsWith('text')) return 'text';
	return 'image';
}

export function clockText(seconds: number): string {
	const whole = Math.max(0, Math.round(seconds));
	const hours = Math.floor(whole / 3600);
	const minutes = Math.floor((whole % 3600) / 60);
	const rest = String(whole % 60).padStart(2, '0');
	return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${rest}` : `${minutes}:${rest}`;
}

function durationText(kind: EntryKind, seconds: number | null | undefined): string | null {
	if ((kind !== 'video' && kind !== 'audio') || typeof seconds !== 'number' || seconds <= 0) return null;
	return clockText(seconds);
}

export function historyThumb(generation: GenerationHistoryItem, file: GenerationFile): string | null {
	const kind = kindOf(file.file_type);
	if (kind === 'audio' || kind === 'mesh') return null;
	if (isBrowserUrl(file.thumbnail_medium)) return file.thumbnail_medium;
	const filename = (file.file_path ?? '').split('/').pop() || file.file_path;
	return api.getGenerationThumbnailURL(generation.id, filename, 'medium');
}

export function entryFromHistory(generation: GenerationHistoryItem, file: GenerationFile): PickerEntry {
	const picked = pickedFromHistoryFile(generation, file);
	const { label, sublabel } = historyTileLabel(generation, file);
	const filename = picked.filename ?? '';
	const kind = kindOf(file.file_type);
	return {
		key: picked.key,
		kind,
		label,
		accessibleName: filename && filename !== label ? `${filename} (${label})` : label,
		sublabel,
		ratio: ratioOf(file.width, file.height),
		thumb: historyThumb(generation, file),
		duration: durationText(kind, file.duration_seconds),
		text: null,
		picked
	};
}

export function historyEntries(
	generations: readonly GenerationHistoryItem[],
	kinds: readonly PickerMediaType[]
): PickerEntry[] {
	const entries: PickerEntry[] = [];
	for (const generation of generations) {
		const files = kinds.length === 1 ? filterFilesByMediaType(generation.files, kinds[0]) : (generation.files ?? []);
		for (const file of files) {
			const kind = kindOf(file.file_type);
			if (kind === 'text' || !kinds.includes(kind as PickerMediaType)) continue;
			entries.push(entryFromHistory(generation, file));
		}
	}
	return entries;
}

export function entryFromLibrary(item: LibraryItem): PickerEntry {
	const picked = pickedFromLibraryItem(item);
	const kind = kindOf(item.media_type);
	const date = typeof item.created_at === 'string' ? item.created_at.slice(0, 10) : '';
	const size = typeof item.size === 'number' && item.size > 0 ? formatBytes(item.size) : '';
	const label = libraryItemDisplayName(item);
	return {
		key: picked.key,
		kind,
		label,
		accessibleName: label,
		sublabel: [size, date].filter(Boolean).join(' · '),
		ratio: kind === 'audio' ? DEFAULT_RATIO : ratioOf(item.width, item.height),
		thumb: kind === 'audio' ? null : (libraryItemGridSrc(item) ?? null),
		duration: durationText(kind, item.duration_seconds),
		text: null,
		picked
	};
}

export function libraryEntries(items: readonly LibraryItem[], kinds: readonly PickerMediaType[]): PickerEntry[] {
	return items
		.filter((item) => {
			const kind = kindOf(item.media_type);
			return kind !== 'text' && kinds.includes(kind as PickerMediaType);
		})
		.map(entryFromLibrary);
}

export function entryFromKitItem(kit: Pick<Kit, 'id' | 'name' | 'handle'>, item: KitItem): PickerEntry {
	const picked = pickedFromKitItem(kit, item);
	const kind = kindOf(item.kind);
	const meta =
		kind === 'video' || kind === 'audio'
			? (durationText(kind, item.duration_seconds) ?? '')
			: item.width && item.height
				? `${item.width} × ${item.height}`
				: '';
	return {
		key: kitItemKey(item.id),
		kind,
		label: item.name,
		accessibleName: item.name,
		sublabel: meta,
		ratio: kind === 'image' || kind === 'video' ? ratioOf(item.width, item.height) : DEFAULT_RATIO,
		thumb: item.missing ? null : (item.thumbnail_url ?? (kind === 'image' ? item.url : null)),
		duration: durationText(kind, item.duration_seconds),
		text: kind === 'text' ? (item.text ?? '') : null,
		picked
	};
}
