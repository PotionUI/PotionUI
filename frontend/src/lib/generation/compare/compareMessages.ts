import type { GridCell } from './types';

type Message = Record<string, any>;

export const COMPARE_MESSAGE_TYPES = new Set([
	'generation_status',
	'workbench_update',
	'gallery_update',
	'generation_complete',
	'generation_error',
	'generation_cancelled'
]);

export const COMPARE_TERMINAL_TYPES = new Set(['generation_complete', 'generation_error', 'generation_cancelled']);

function mediaUrl(raw: unknown): string | null {
	if (typeof raw !== 'string' || raw === '') return null;
	if (raw.startsWith('/api/') || raw.startsWith('data:')) return raw;
	if (raw.startsWith('http')) return raw.replace(/^https?:\/\/[^/]+/, '');
	return `data:image/png;base64,${raw}`;
}

function firstUrl(...candidates: unknown[]): string | null {
	for (const candidate of candidates) {
		const url = mediaUrl(candidate);
		if (url) return url;
	}
	return null;
}

function progressOf(message: Message): GridCell['progress'] {
	const step = Number(message.current_step_num);
	const total = Number(message.total_steps);
	if (Number.isFinite(step) && Number.isFinite(total) && total > 0) return { step, total };
	const fraction = message.progress;
	if (typeof fraction === 'number' && Number.isFinite(fraction)) {
		return { step: Math.round(Math.min(1, Math.max(0, fraction)) * 100), total: 100, percent: true };
	}
	return null;
}

export function userFacingError(message: Message): string {
	const raw = message.message ?? message.error ?? message.data?.message ?? message.data?.error;
	return typeof raw === 'string' && raw.trim() !== '' ? raw : 'Generation failed';
}

export function generationIdOf(message: Message): string | undefined {
	if (COMPARE_TERMINAL_TYPES.has(message.type)) {
		return message.data?.id || message.data?.generation_id || message.generation_id;
	}
	return message.generation_id;
}

export function applyCellMessage(cell: GridCell, message: Message): GridCell {
	switch (message.type) {
		case 'generation_status': {
			if (cell.status === 'completed' || cell.status === 'failed' || cell.status === 'cancelled') return cell;
			const pending = message.status === 'pending' || message.status === 'queued';
			return {
				...cell,
				status: pending ? 'queued' : 'running',
				progress: pending ? null : (progressOf(message) ?? cell.progress)
			};
		}
		case 'workbench_update': {
			if (cell.status === 'completed' || cell.status === 'failed' || cell.status === 'cancelled') return cell;
			if (message.preview_suppressed === true) return { ...cell, status: 'running', previewUrl: null };
			if (message.file_type && message.file_type !== 'image') return { ...cell, status: 'running' };
			const preview = firstUrl(message.image);
			return { ...cell, status: 'running', previewUrl: preview ?? cell.previewUrl };
		}
		case 'gallery_update': {
			const isVideo = Array.isArray(message.videos) && message.videos.length > 0;
			const thumbnail = isVideo
				? firstUrl(
						message.video_urls_list?.[0]?.thumbnail,
						message.video_urls_list?.[0]?.original,
						message.video_urls_list?.[0]?.path,
						message.video_urls_list?.[0]?.url,
						message.videos?.[0]?.url
					)
				: firstUrl(
						message.image_urls_list?.[0]?.thumbnail,
						message.image_urls_list?.[0]?.original,
						message.image_urls_list?.[0]?.path,
						message.images?.[0]
					);
			if (message.preview_suppressed === true) return { ...cell, thumbnailUrl: null };
			return {
				...cell,
				thumbnailUrl: thumbnail ?? cell.thumbnailUrl,
				mediaType: isVideo ? 'video' : thumbnail ? 'image' : cell.mediaType
			};
		}
		case 'generation_complete': {
			const cancelled = message.data?.status === 'cancelled';
			return {
				...cell,
				status: cancelled ? 'cancelled' : 'completed',
				progress: null,
				previewUrl: null
			};
		}
		case 'generation_error':
			return { ...cell, status: 'failed', progress: null, previewUrl: null, error: userFacingError(message) };
		case 'generation_cancelled':
			return { ...cell, status: 'cancelled', progress: null, previewUrl: null };
		default:
			return cell;
	}
}
