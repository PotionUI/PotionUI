import type { AppNotification } from '$lib/services/api/notifications';

type FailureNotification = Pick<AppNotification, 'type' | 'message' | 'metadata'>;

export function generationFailureErrorId(notification: FailureNotification): string | null {
	if (notification.type !== 'generation.failed') return null;
	const errorId = notification.metadata?.error_id;
	return typeof errorId === 'string' && errorId ? errorId : null;
}

export function generationFailureToastMessage(notification: FailureNotification): string {
	if (notification.type !== 'generation.failed') return notification.message || '';
	const hint = notification.metadata?.hint;
	const errorId = generationFailureErrorId(notification);
	return [
		notification.message || null,
		typeof hint === 'string' && hint.trim() ? hint : null,
		errorId ? 'Give this to your admin.' : null
	]
		.filter((part): part is string => Boolean(part))
		.join('\n\n');
}
