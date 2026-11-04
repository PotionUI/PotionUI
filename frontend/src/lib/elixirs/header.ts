const FAILED_STEP_STATUSES = ['failed', 'no_output'];

export const TIERS = ['wide', 'mid', 'compact', 'minimal'];

export interface SettleTierOptions {
	start: string;
	available: number;
	apply: (tier: string) => unknown;
	measure: () => number;
	margin?: number;
}

export interface StepView {
	view?: { status?: string } | null;
}

export interface ProgressSegment {
	text: string;
	tone: 'neutral' | 'danger';
}

export async function settleTier({ start, available, apply, measure, margin = 24 }: SettleTierOptions): Promise<string> {
	let index = Math.max(0, TIERS.indexOf(start));
	await apply(TIERS[index]);
	while (index < TIERS.length - 1 && measure() > available) {
		index += 1;
		await apply(TIERS[index]);
	}
	while (index > 0) {
		await apply(TIERS[index - 1]);
		if (measure() + margin <= available) {
			index -= 1;
		} else {
			await apply(TIERS[index]);
			break;
		}
	}
	return TIERS[index];
}

export function doneCount(steps: StepView[]): number {
	return steps.filter((step) => step.view?.status === 'done').length;
}

export function failedCount(steps: StepView[]): number {
	return steps.filter((step) => FAILED_STEP_STATUSES.includes(step.view?.status ?? '')).length;
}

export function progressSegments(name: string, done: number, total: number, failed: number, elapsed: string | null | undefined, tier: string): ProgressSegment[] {
	const parts: ProgressSegment[] = [{ text: name, tone: 'neutral' }];
	if (tier === 'wide' || tier === 'mid') parts.push({ text: `${done}/${total}`, tone: 'neutral' });
	if (failed > 0) parts.push({ text: `${failed} failed`, tone: 'danger' });
	if (elapsed) parts.push({ text: elapsed, tone: 'neutral' });
	return parts;
}
