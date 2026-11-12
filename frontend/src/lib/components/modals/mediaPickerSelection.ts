import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';
import type { LibraryItem } from '$lib/services/api/library';
import type { Kit, KitItem } from '$lib/kits/types';
import { get, writable, type Readable } from 'svelte/store';

export type PickerMediaType = 'image' | 'video' | 'audio' | 'mesh';

export type PickedMediaOrigin =
	| { kind: 'history'; generationId: string; fileId: number }
	| { kind: 'library'; itemId: string }
	| { kind: 'kit'; kitId: string; itemId: string };

export interface PickedMedia {
	key: string;
	mediaType: string | null;
	filename: string | null;
	origin: PickedMediaOrigin;
	generation?: GenerationHistoryItem;
	file?: GenerationFile;
	item?: LibraryItem;
	kit?: Pick<Kit, 'id' | 'name' | 'handle'>;
	kitItem?: KitItem;
}

export function historyMediaKey(generationId: string, fileId: number): string {
	return `${generationId}:${fileId}`;
}

export function libraryMediaKey(itemId: string): string {
	return itemId;
}

export function mediaTypeOfFile(file: { file_type?: string | null }): string {
	const type = (file.file_type ?? '').toLowerCase();
	for (const known of ['image', 'video', 'audio', 'mesh']) {
		if (type.startsWith(known)) return known;
	}
	return type;
}

export function pickedFromHistoryFile(generation: GenerationHistoryItem, file: GenerationFile): PickedMedia {
	return {
		key: historyMediaKey(generation.id, file.id),
		mediaType: mediaTypeOfFile(file),
		filename: (file.file_path ?? '').split('/').pop() || file.file_path,
		origin: { kind: 'history', generationId: generation.id, fileId: file.id },
		generation,
		file
	};
}

const PROMPT_KEYS = ['prompt', 'positive_prompt', 'text'];
const LABEL_EXCERPT = 40;

function excerpt(text: string): string {
	const flat = text.replace(/\s+/g, ' ').trim();
	return flat.length > LABEL_EXCERPT ? `${flat.slice(0, LABEL_EXCERPT - 1).trimEnd()}…` : flat;
}

export function historyTileLabel(
	generation: GenerationHistoryItem,
	file: GenerationFile
): { label: string; sublabel: string } {
	const form = (generation.form_data ?? {}) as Record<string, unknown>;
	let prompt = '';
	for (const key of PROMPT_KEYS) {
		const value = form[key];
		if (typeof value === 'string' && value.trim()) {
			prompt = excerpt(value);
			break;
		}
	}
	const filename = (file.file_path ?? '').split('/').pop() || file.file_path || '';
	const date = typeof generation.created_at === 'string' ? generation.created_at.slice(0, 10) : '';
	const preset = generation.preset_name ?? '';
	return {
		label: prompt || preset || filename,
		sublabel: [prompt ? preset : '', date].filter(Boolean).join(' · ')
	};
}

export function pickedFromLibraryItem(item: LibraryItem): PickedMedia {
	return {
		key: libraryMediaKey(item.id),
		mediaType: item.media_type,
		filename: item.original_filename || item.filename,
		origin: { kind: 'library', itemId: item.id },
		item
	};
}

export function pickedFromKitItem(kit: Pick<Kit, 'id' | 'name' | 'handle'>, item: KitItem): PickedMedia {
	return {
		key: `kit:${item.id}`,
		mediaType: item.kind,
		filename: item.name,
		origin: { kind: 'kit', kitId: kit.id, itemId: item.id },
		kit,
		kitItem: item
	};
}

export function isMediaTypeSelectable(
	mediaType: string,
	allowed: readonly PickerMediaType[] | undefined
): boolean {
	return !allowed || allowed.length === 0 || (allowed as readonly string[]).includes(mediaType);
}

export function mediaTypeConstraintMessage(allowed: readonly PickerMediaType[] | undefined): string {
	if (!allowed || allowed.length === 0) return '';
	return `Only ${allowed.join(' and ')} files can be selected here`;
}

export function toggleKey(keys: readonly string[], key: string): string[] {
	return keys.includes(key) ? keys.filter((k) => k !== key) : [...keys, key];
}

export function sameKeys(a: readonly string[], b: readonly string[]): boolean {
	return a.length === b.length && a.every((key, i) => key === b[i]);
}

export type PickerSource = 'history' | 'library';

export function pickedFromKey(key: string, source: PickerSource | 'any'): PickedMedia {
	if (source === 'any') {
		if (key.startsWith('kit:')) {
			return { key, mediaType: null, filename: null, origin: { kind: 'kit', kitId: '', itemId: key.slice(4) } };
		}
		return pickedFromKey(key, key.includes(':') ? 'history' : 'library');
	}
	if (source === 'library') {
		return { key, mediaType: null, filename: null, origin: { kind: 'library', itemId: key } };
	}
	const split = key.lastIndexOf(':');
	return {
		key,
		mediaType: null,
		filename: null,
		origin: {
			kind: 'history',
			generationId: split === -1 ? key : key.slice(0, split),
			fileId: split === -1 ? NaN : Number(key.slice(split + 1))
		}
	};
}

export function itemsForKeys(
	keys: readonly string[],
	known: ReadonlyMap<string, PickedMedia>,
	source: PickerSource | 'any'
): PickedMedia[] {
	return keys.map((key) => known.get(key) ?? pickedFromKey(key, source));
}

export type SelectionChangeHandler = ((keys: string[], items: PickedMedia[]) => void) | undefined;

export interface MediaSelectionController {
	selection: Readable<string[]>;
	follow: (keys: string[] | undefined) => void;
	track: (isOpen: boolean) => void;
	remember: (items: PickedMedia[]) => void;
	toggle: (key: string, onChange: SelectionChangeHandler) => void;
	set: (keys: string[], onChange: SelectionChangeHandler) => void;
	resolve: (keys: readonly string[]) => PickedMedia[];
	cancel: (onChange: SelectionChangeHandler) => void;
	confirm: (onConfirm: SelectionChangeHandler) => boolean;
}

export function createMediaSelection(source: PickerSource | 'any'): MediaSelectionController {
	const selection = writable<string[]>([]);
	const known = new Map<string, PickedMedia>();
	let baseline: string[] = [];
	let wasOpen = false;

	function emit(keys: string[], onChange: SelectionChangeHandler) {
		selection.set(keys);
		onChange?.(keys, itemsForKeys(keys, known, source));
	}

	return {
		selection: { subscribe: selection.subscribe },
		follow(keys) {
			if (keys) selection.set(keys);
		},
		track(isOpen) {
			if (isOpen === wasOpen) return;
			wasOpen = isOpen;
			if (isOpen) baseline = [...get(selection)];
		},
		remember(items) {
			for (const item of items) known.set(item.key, item);
		},
		toggle(key, onChange) {
			emit(toggleKey(get(selection), key), onChange);
		},
		set(keys, onChange) {
			emit([...keys], onChange);
		},
		resolve(keys) {
			return itemsForKeys(keys, known, source);
		},
		cancel(onChange) {
			if (!sameKeys(get(selection), baseline)) emit([...baseline], onChange);
		},
		confirm(onConfirm) {
			const keys = get(selection);
			if (keys.length === 0) return false;
			onConfirm?.(keys, itemsForKeys(keys, known, source));
			return true;
		}
	};
}
