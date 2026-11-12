import { writable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import { toasts } from '$lib/stores/toast';
import { KIT_ITEM_LIMIT } from '$lib/kits/types';
import { picksFromHistoryFiles, type KitPick } from '$lib/kits/kitPicks';
import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';

export const kitAddRequest = writable<KitPick[] | null>(null);

export function requestAddToKit(files: readonly Pick<GenerationFile, 'id' | 'file_path' | 'file_type'>[]): boolean {
	const picks = picksFromHistoryFiles(files, (fileId) => api.getFileURL(fileId, 'small')).slice(0, KIT_ITEM_LIMIT);
	if (picks.length === 0) {
		toasts.error('Kits hold pictures, clips, sounds and notes.');
		return false;
	}
	kitAddRequest.set(picks);
	return true;
}

export function clearKitAddRequest(): void {
	kitAddRequest.set(null);
}

export function requestAddGenerationsToKit(generations: readonly Pick<GenerationHistoryItem, 'files'>[]): boolean {
	const files = generations.flatMap((generation) => (generation.files ?? []).filter((file) => file.is_final !== false));
	return requestAddToKit(files);
}
