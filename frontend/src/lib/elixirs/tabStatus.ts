import type { ToolTabStatus } from '$lib/types/tabs';

export interface StatusRun {
	status?: string | null;
}

export interface StatusStep {
	view?: { status?: string | null } | null;
}

const BREWING = new Set(['pending', 'running', 'cancelling']);
const FAILED = new Set(['needs_attention', 'failed', 'interrupted']);
const FINISHED = new Set(['completed', 'partial']);

export function stepCounts(steps: readonly StatusStep[]): [number, number] {
	return [steps.filter((step) => step.view?.status === 'done').length, steps.length];
}

export function elixirTabStatus(
	run: StatusRun | null | undefined,
	steps: readonly StatusStep[],
	options: { background?: boolean; wasBrewing?: boolean } = {}
): ToolTabStatus | null {
	const status = run?.status ?? null;
	if (!status) return null;
	if (BREWING.has(status)) {
		const [done, total] = stepCounts(steps);
		return {
			tone: 'signal',
			label: total > 0 ? `Brewing, ${done} of ${total} steps done` : 'Brewing',
			count: total > 0 ? [done, total] : null,
			pulse: true
		};
	}
	if (status === 'awaiting_selection') return { tone: 'signal', label: 'Waiting for your choice', ring: true, pulse: true };
	if (FAILED.has(status)) return { tone: 'danger', label: status === 'needs_attention' ? 'Needs attention' : 'Brew failed' };
	if (FINISHED.has(status) && options.background && options.wasBrewing) {
		return { tone: 'success', label: status === 'partial' ? 'Brew partly done' : 'Brew done' };
	}
	return null;
}

export function isBrewingStatus(status: ToolTabStatus | null | undefined): boolean {
	return !!status && status.tone === 'signal';
}
