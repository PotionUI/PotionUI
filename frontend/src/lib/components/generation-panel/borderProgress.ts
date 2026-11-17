import { deriveDeckState } from './deckStatus';

export interface BorderRun {
	status: string;
	progress?: number | null;
}

export interface BorderProgress {
	state: 'idle' | 'loading' | 'running';
	percent: number | null;
}

export function borderProgressOf(run: BorderRun | null | undefined): BorderProgress {
	const active = !!run && (run.status === 'queued' || run.status === 'running');
	const hasFraction = !!run && run.status === 'running' && typeof run.progress === 'number' && Number.isFinite(run.progress);
	const state = deriveDeckState({ isGenerating: active, hasProgressFraction: hasFraction, disabled: false });
	if (state === 'running') return { state, percent: Math.round(Math.min(1, Math.max(0, run!.progress as number)) * 100) };
	if (state === 'loading') return { state, percent: null };
	return { state: 'idle', percent: null };
}
