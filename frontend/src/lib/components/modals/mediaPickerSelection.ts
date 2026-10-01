import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';
import type { LibraryItem } from '$lib/services/api/library';
import { get, writable, type Readable } from 'svelte/store';

export type PickerMediaType = 'image' | 'video' | 'audio' | 'mesh';

export type PickedMediaOrigin =
	| { kind: 'history'; generationId: string; fileId: number }
	| { kind: 'library'; itemId: string };

export interface PickedMedia {
	key: string;
	mediaType: string | null;
	filename: string | null;
	origin: PickedMediaOrigin;
	generation?: GenerationHistoryItem;
	file?: GenerationFile;
	item?: LibraryItem;
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

export function pickedFromLibraryItem(item: LibraryItem): PickedMedia {
	return {
		key: libraryMediaKey(item.id),
		mediaType: item.media_type,
		filename: item.original_filename || item.filename,
		origin: { kind: 'library', itemId: item.id },
		item
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

export function pickedFromKey(key: string, source: PickerSource): PickedMedia {
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
	source: PickerSource
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
	cancel: (onChange: SelectionChangeHandler) => void;
	confirm: (onConfirm: SelectionChangeHandler) => boolean;
}

export function createMediaSelection(source: PickerSource): MediaSelectionController {
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
