export type ModelDownloadPhase = 'idle' | 'starting' | 'polling' | 'completed' | 'failed' | 'forbidden';

export interface ModelDownloadState {
	phase: ModelDownloadPhase;
	downloadId: string | null;
	progress: number | null;
	error: string | null;
	gated: boolean;
	gatedRepo: string | null;
}

export const initialModelDownloadState: ModelDownloadState = {
	phase: 'idle',
	downloadId: null,
	progress: null,
	error: null,
	gated: false,
	gatedRepo: null
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
			gated?: boolean;
			gatedRepo?: string | null;
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
			return { ...initialModelDownloadState, phase: 'starting' };
		case 'started':
			return { ...initialModelDownloadState, phase: 'polling', downloadId: event.downloadId, progress: 0 };
		case 'forbidden':
			return { ...initialModelDownloadState, phase: 'forbidden' };
		case 'poll':
			if (event.status === 'completed') {
				return { ...state, phase: 'completed', progress: 1, error: null };
			}
			if (event.status === 'failed') {
				return {
					...state,
					phase: 'failed',
					progress: event.progress,
					error: event.error || 'Download failed',
					gated: event.gated === true,
					gatedRepo: event.gatedRepo ?? null
				};
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
