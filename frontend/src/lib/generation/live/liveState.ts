import { parsePreviewClip, type PreviewClip } from '../previewClip';

export type LiveStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface LiveGeneration {
	id: string;
	status: LiveStatus;
	progress: number | null;
	currentStep: string | null;
	message: string | null;
	pipeName: string | null;
	pipeId: number | null;
	itemIndex: number | null;
	itemCount: number | null;
	preview: string | null;
	clip: PreviewClip | null;
	previewSuppressed: boolean;
	galleryUpdates: number;
	seed: number | null;
	error: string | null;
}

export interface LiveMessage {
	type: string;
	[key: string]: unknown;
}

export function isTerminalLive(status: LiveStatus): boolean {
	return status === 'completed' || status === 'failed' || status === 'cancelled';
}

export function initialLive(id: string, status: LiveStatus = 'running'): LiveGeneration {
	return {
		id,
		status,
		progress: null,
		currentStep: null,
		message: null,
		pipeName: null,
		pipeId: null,
		itemIndex: null,
		itemCount: null,
		preview: null,
		clip: null,
		previewSuppressed: false,
		galleryUpdates: 0,
		seed: null,
		error: null
	};
}

function text(value: unknown): string | null {
	return typeof value === 'string' && value !== '' ? value : null;
}

function count(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function previewSource(image: unknown): string | null {
	if (typeof image !== 'string' || image === '') return null;
	if (image.startsWith('/api/') || image.startsWith('data:')) return image;
	if (image.startsWith('http')) return image.replace(/^https?:\/\/[^/]+/, '');
	return `data:image/png;base64,${image}`;
}

function errorText(message: LiveMessage): string | null {
	const data = (message.data ?? null) as { message?: unknown; error?: unknown } | null;
	return text(message.message) ?? text(message.error) ?? text(data?.message) ?? text(data?.error);
}

export function applyLiveMessage(state: LiveGeneration, message: LiveMessage): LiveGeneration {
	switch (message.type) {
		case 'generation_status': {
			if (isTerminalLive(state.status)) return state;
			return {
				...state,
				status: message.status === 'pending' ? 'pending' : 'running',
				progress: count(message.progress) ?? state.progress,
				currentStep: text(message.current_step) ?? state.currentStep,
				message: text(message.message) ?? state.message,
				pipeName: text(message.pipe_name) ?? state.pipeName,
				pipeId: count(message.pipe_id) ?? state.pipeId,
				itemIndex: count(message.item_index) ?? state.itemIndex,
				itemCount: count(message.item_count) ?? state.itemCount
			};
		}
		case 'workbench_update': {
			if (isTerminalLive(state.status)) return state;
			if (message.preview_suppressed === true) return { ...state, preview: null, previewSuppressed: true };
			if (message.file_type && message.file_type !== 'image') return state;
			const source = previewSource(message.image);
			if (!source) return state;
			if (message.replayed === true && state.preview) return state;
			return { ...state, preview: source, previewSuppressed: false };
		}
		case 'workbench_preview_clip': {
			if (isTerminalLive(state.status)) return state;
			if (message.preview_suppressed === true) return { ...state, clip: null, previewSuppressed: true };
			const clip = parsePreviewClip(message);
			if (!clip) return state;
			if (message.replayed === true && state.clip) return state;
			return { ...state, clip, previewSuppressed: false };
		}
		case 'gallery_update':
			return { ...state, galleryUpdates: state.galleryUpdates + 1 };
		case 'pipe_artifact': {
			if (message.artifact_type !== 'seed') return state;
			const data = (message.artifact_data ?? null) as { seed?: unknown } | null;
			const seed = count(data?.seed);
			return seed === null ? state : { ...state, seed };
		}
		case 'generation_complete':
			return { ...state, status: 'completed', progress: 1 };
		case 'generation_error':
			return { ...state, status: 'failed', error: errorText(message) };
		case 'generation_cancelled':
			return { ...state, status: 'cancelled' };
		default:
			return state;
	}
}
