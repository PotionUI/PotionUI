import type { ChainPlan } from './plan';

export type PreviewMode = 'off' | 'live' | 'server';

export interface LiveInput {
	webgl2: boolean;
	contextLosses: number;
	plan: Pick<ChainPlan, 'effects' | 'skipped' | 'measuring'> | null;
	steppedDown: boolean;
	playing: boolean;
}

export interface LiveState {
	mode: PreviewMode;
	canvas: boolean;
	serverPaused: boolean;
	showClip: boolean;
	chips: string[];
}

export const MAX_CONTEXT_LOSSES = 2;

export function measuringChip(groups: readonly string[]): string | null {
	if (!groups.length) return null;
	return groups.includes('colour') ? 'Measuring colours…' : 'Measuring the clip…';
}

export function liveState(input: LiveInput): LiveState {
	const effects = input.plan?.effects ?? 0;
	if (!input.webgl2 || input.contextLosses >= MAX_CONTEXT_LOSSES) {
		return { mode: 'server', canvas: false, serverPaused: effects > 0 && !input.playing, showClip: true, chips: [] };
	}
	if (!input.plan || effects === 0) return { mode: 'off', canvas: false, serverPaused: false, showClip: false, chips: [] };
	const partial = input.plan.skipped.length > 0;
	const chips: string[] = [];
	const labels = [...new Set(input.plan.skipped.map((entry) => entry.label))];
	if (labels.length) chips.push(`${labels.join(', ')} ${labels.length > 1 ? 'show' : 'shows'} when paused`);
	const measuring = measuringChip(input.plan.measuring);
	if (measuring) chips.push(measuring);
	if (input.steppedDown) chips.push('Preview at lower resolution');
	const serverPaused = partial && !input.playing;
	return { mode: 'live', canvas: !serverPaused, serverPaused, showClip: partial, chips };
}
