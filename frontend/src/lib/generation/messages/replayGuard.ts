export type PreviewChannel = 'frame' | 'clip';

const MAX_REMEMBERED = 64;
const seen = new Map<string, 'live' | 'replayed'>();

function remember(key: string, state: 'live' | 'replayed'): void {
	seen.delete(key);
	seen.set(key, state);
	if (seen.size > MAX_REMEMBERED) {
		const oldest = seen.keys().next().value;
		if (oldest !== undefined) seen.delete(oldest);
	}
}

export function shouldApplyPreview(
	generationId: string | undefined,
	channel: PreviewChannel,
	message: { replayed?: unknown }
): boolean {
	if (!generationId) return true;
	const key = `${generationId}:${channel}`;
	if (message.replayed === true) {
		if (seen.has(key)) return false;
		remember(key, 'replayed');
		return true;
	}
	remember(key, 'live');
	return true;
}

export function resetPreviewReplay(generationId: string): void {
	seen.delete(`${generationId}:frame`);
	seen.delete(`${generationId}:clip`);
}

export function resetPreviewReplayForTests(): void {
	seen.clear();
}
