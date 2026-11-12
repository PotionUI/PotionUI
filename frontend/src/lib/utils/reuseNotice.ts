import { toasts } from '$lib/stores/toast';
import type { HistoryReuseResult } from '$lib/utils/historyReuse';

export function reuseNoticeMessage(result: Pick<HistoryReuseResult, 'converted'>): string | null {
	const converted = result.converted;
	if (!converted) return null;
	const head = 'Long video is now Video Director: your setup was opened as shots.';
	return converted.notes.length > 0 ? `${head} ${converted.notes.join(' ')}` : head;
}

export function announceReuseConversion(result: Pick<HistoryReuseResult, 'converted'>): void {
	const message = reuseNoticeMessage(result);
	if (message) toasts.warning(message, 12000);
}
