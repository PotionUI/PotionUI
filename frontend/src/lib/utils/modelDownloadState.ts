export type ModelDownloadPhase = 'idle' | 'starting' | 'polling' | 'completed' | 'failed' | 'forbidden';

export interface ModelDownloadState {
	phase: ModelDownloadPhase;
	downloadId: string | null;
	progress: number | null;
	error: string | null;
}

export const initialModelDownloadState: ModelDownloadState = {
	phase: 'idle',
	downloadId: null,
	progress: null,
	error: null
};

export type ModelDownloadEvent =
	| { type: 'start' }
	| { type: 'started'; downloadId: string }
	| { type: 'forbidden' }
	| {
			type: 'poll';
			status: 'pending' | 'running' | 'completed' | 'failed';
			progress: number | null;
			error: string | null;
	  }
	| { type: 'error'; message: string }
	| { type: 'reset' };

/** Pure state machine driving the model picker's per-recommendation download UI. */
export function reduceModelDownloadState(
	state: ModelDownloadState,
	event: ModelDownloadEvent
): ModelDownloadState {
	switch (event.type) {
		case 'start':
			return { phase: 'starting', downloadId: null, progress: null, error: null };
		case 'started':
			return { phase: 'polling', downloadId: event.downloadId, progress: 0, error: null };
		case 'forbidden':
			return { phase: 'forbidden', downloadId: null, progress: null, error: null };
		case 'poll':
			if (event.status === 'completed') {
				return { ...state, phase: 'completed', progress: 1, error: null };
			}
			if (event.status === 'failed') {
				return { ...state, phase: 'failed', progress: event.progress, error: event.error || 'Download failed' };
			}
			return { ...state, phase: 'polling', progress: event.progress };
		case 'error':
			return { ...state, phase: 'failed', error: event.message };
		case 'reset':
			return { ...initialModelDownloadState };
		default:
			return state;
	}
}

export function shouldContinuePolling(phase: ModelDownloadPhase): boolean {
	return phase === 'polling';
}
