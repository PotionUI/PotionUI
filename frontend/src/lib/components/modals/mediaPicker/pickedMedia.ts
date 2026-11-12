import type { LibraryItem } from '$lib/services/api/library';
import type { KitItem } from '$lib/kits/types';
import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';
import type { PickedMedia } from '../mediaPickerSelection';

function uploadFilename(url: string | null, fallback: string): string {
	if (!url) return fallback;
	const clean = url.split('?')[0];
	const marker = clean.lastIndexOf('/uploads/');
	const tail = marker >= 0 ? clean.slice(marker + '/uploads/'.length) : clean.split('/').pop();
	return tail || fallback;
}

export function libraryItemFromKitItem(item: KitItem): LibraryItem {
	const filename = uploadFilename(item.url, item.name);
	return {
		id: item.media_ref ?? item.id,
		filename,
		original_filename: item.name,
		media_type: item.kind,
		url: item.url ?? '',
		thumbnail_small: item.thumbnail_url ?? undefined,
		thumbnail_medium: item.thumbnail_url ?? undefined,
		width: item.width ?? undefined,
		height: item.height ?? undefined,
		duration_seconds: item.duration_seconds ?? undefined,
		tags: []
	};
}

export interface PickedHandlers {
	history: (generation: GenerationHistoryItem, file: GenerationFile) => void;
	library: (item: LibraryItem) => void;
}

export function libraryItemOf(picked: PickedMedia): LibraryItem | null {
	if (picked.item) return picked.item;
	if (picked.kitItem && picked.kitItem.kind !== 'text') return libraryItemFromKitItem(picked.kitItem);
	return null;
}

export function routePicked(picked: PickedMedia, handlers: PickedHandlers): void {
	if (picked.generation && picked.file) {
		handlers.history(picked.generation, picked.file);
		return;
	}
	const item = libraryItemOf(picked);
	if (item) handlers.library(item);
}
