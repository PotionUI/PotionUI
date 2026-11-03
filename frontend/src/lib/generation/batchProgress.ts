import type { ProgressData } from '$lib/types/tabs';

export interface BatchProgressReading {
	label: string;
	percent: number | null;
}

const ITEM_NOUNS: Record<string, string> = { image: 'Image', video: 'Video', audio: 'Audio' };

export function readBatchProgress(
	progress: Pick<ProgressData, 'item_index' | 'item_count' | 'item_progress' | 'prompt_index' | 'prompt_count'> | null | undefined,
	category?: string | null
): BatchProgressReading | null {
	if (!progress) return null;
	const { item_index, item_count, item_progress, prompt_index, prompt_count } = progress;
	const percent =
		typeof item_progress === 'number' && Number.isFinite(item_progress)
			? Math.round(Math.min(1, Math.max(0, item_progress)) * 100)
			: null;
	if (typeof prompt_index === 'number' && typeof prompt_count === 'number' && prompt_count > 1) {
		return { label: `Prompt ${prompt_index + 1} of ${prompt_count}`, percent };
	}
	if (typeof item_index === 'number' && typeof item_count === 'number' && item_count > 1) {
		return { label: `${ITEM_NOUNS[category ?? ''] ?? 'Item'} ${item_index + 1} of ${item_count}`, percent };
	}
	return null;
}
