/** Pure derivation helpers for the generation console bar. Kept
 *  side-effect free so the state machine for the mark and the mode-chrome
 *  glyph can be unit tested without mounting Svelte components. */

export type GenerationMode = 'once' | 'forever';

/** Visual/interaction state of the 48px generate mark (GenerateMark.svelte).
 *  `running` always wins over `disabled` — a job in flight is always
 *  cancellable, even if `canGenerate` would otherwise be false. */
export type MarkState = 'ready' | 'running' | 'continuous-armed' | 'disabled';

export function deriveMarkState(params: {
	isGenerating: boolean;
	canGenerate: boolean;
	mode: GenerationMode;
}): MarkState {
	if (params.isGenerating) return 'running';
	if (!params.canGenerate) return 'disabled';
	return params.mode === 'forever' ? 'continuous-armed' : 'ready';
}

/** The mode chrome button's glyph never becomes a second cancel (mock rule,
 *  generation-panel.dc.html line 415): loop while idle, pause during a
 *  continuous run, a disabled hourglass once that stop is requested. */
export type ModeChromeGlyph = 'idle' | 'pause' | 'stopping';

export function deriveModeChromeGlyph(params: {
	isGenerating: boolean;
	mode: GenerationMode;
	stopAfterCurrentRequested: boolean;
}): ModeChromeGlyph {
	if (params.stopAfterCurrentRequested) return 'stopping';
	if (params.isGenerating && params.mode === 'forever') return 'pause';
	return 'idle';
}

/** Shared formatter for the `last` and `elapsed` readout cells — "none" for
 *  a value that doesn't exist yet, matching the mock's binding rule that a
 *  cell never disappears (line 415). */
export function formatDurationSeconds(totalSeconds: number | null): string {
	if (totalSeconds === null || !Number.isFinite(totalSeconds)) return 'none';
	if (totalSeconds < 60) return `${totalSeconds.toFixed(1)}s`;
	if (totalSeconds < 3600) {
		const minutes = Math.floor(totalSeconds / 60);
		const seconds = Math.floor(totalSeconds % 60);
		return `${minutes}m ${seconds}s`;
	}
	const hours = Math.floor(totalSeconds / 3600);
	const minutes = Math.floor((totalSeconds % 3600) / 60);
	return `${hours}h ${minutes}m`;
}

export function formatDurationMs(ms: number | null): string {
	return formatDurationSeconds(ms === null ? null : ms / 1000);
}
