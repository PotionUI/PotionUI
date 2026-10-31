export interface PreviewClipAdminDetail {
	format: string;
	bytes: number;
	long_edge: number;
	update: number;
	updates_max: number;
	every: number;
}

export interface PreviewClip {
	generation_id: string;
	image: string;
	frames: number;
	columns: number;
	frame_width: number;
	frame_height: number;
	fps: number;
	step: number;
	total_steps: number;
	source_indices: number[];
	admin_detail: PreviewClipAdminDetail | null;
}

const MAX_FRAMES = 25;

function positiveInt(value: unknown): number | null {
	return typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : null;
}

export function parsePreviewClip(message: any): PreviewClip | null {
	if (!message || typeof message.image !== 'string' || !message.image.startsWith('data:image/')) return null;
	const frames = positiveInt(message.frames);
	const columns = positiveInt(message.columns);
	const frameWidth = positiveInt(message.frame_width);
	const frameHeight = positiveInt(message.frame_height);
	const fps = positiveInt(message.fps);
	if (!frames || !columns || !frameWidth || !frameHeight || !fps || frames > MAX_FRAMES) return null;
	const detail = message.admin_detail;
	return {
		generation_id: String(message.generation_id ?? ''),
		image: message.image,
		frames,
		columns,
		frame_width: frameWidth,
		frame_height: frameHeight,
		fps,
		step: positiveInt(message.step) ?? 0,
		total_steps: positiveInt(message.total_steps) ?? 0,
		source_indices: Array.isArray(message.source_indices) ? message.source_indices : [],
		admin_detail:
			detail && typeof detail === 'object'
				? {
						format: String(detail.format ?? ''),
						bytes: Number(detail.bytes ?? 0),
						long_edge: Number(detail.long_edge ?? 0),
						update: Number(detail.update ?? 0),
						updates_max: Number(detail.updates_max ?? 0),
						every: Number(detail.every ?? 0)
					}
				: null
	};
}

export function frameOrigin(clip: Pick<PreviewClip, 'columns' | 'frame_width' | 'frame_height'>, index: number) {
	return {
		sx: (index % clip.columns) * clip.frame_width,
		sy: Math.floor(index / clip.columns) * clip.frame_height
	};
}

export function wrapFrameIndex(index: number, frames: number): number {
	if (frames <= 0) return 0;
	return ((index % frames) + frames) % frames;
}

export function framesToAdvance(elapsedMs: number, fps: number): number {
	if (elapsedMs <= 0 || fps <= 0) return 0;
	return Math.floor((elapsedMs * fps) / 1000);
}

export function lastFrameIndex(frames: number): number {
	return Math.max(0, frames - 1);
}

export function isBehind(clipStep: number, liveStep: number, every: number): boolean {
	if (!every || !liveStep) return false;
	return liveStep - clipStep > every;
}

export function chipLabel(step: number): string {
	return step > 0 ? `Preview · Step ${step}` : 'Preview';
}

export function formatKilobytes(bytes: number): string {
	return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function adminStatusLine(detail: PreviewClipAdminDetail, frames: number): string {
	const parts = ['Preview', `${frames} frames`];
	if (detail.every > 0) parts.push(`new clip every ${detail.every} steps`);
	parts.push(formatKilobytes(detail.bytes));
	return parts.join(' · ');
}

export function adminFormatChip(clip: PreviewClip): string {
	return `${clip.frame_width}×${clip.frame_height} · ${clip.frames} fr · ${clip.fps} fps`;
}

export function clipCadence(totalSteps: number): number {
	return Math.max(3, Math.floor(totalSteps / 8));
}
