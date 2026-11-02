export const PREVIEW_CAPS = [1920, 1280, 960] as const;
export const FRAME_BUDGET_MS = 8;
export const SLOW_PASS_MS = 16;
export const BUDGET_WINDOW = 30;

export class BudgetMeter {
	private level = 0;
	private over = 0;
	private slowRuns = new Map<string, number>();
	private slowTools = new Set<string>();

	constructor(
		private readonly limit = FRAME_BUDGET_MS,
		private readonly window = BUDGET_WINDOW
	) {}

	get cap(): number {
		return PREVIEW_CAPS[this.level];
	}

	get steppedDown(): boolean {
		return this.level > 0;
	}

	get slow(): ReadonlySet<string> {
		return this.slowTools;
	}

	frame(totalMs: number): boolean {
		this.over = totalMs > this.limit ? this.over + 1 : 0;
		if (this.over < this.window || this.level >= PREVIEW_CAPS.length - 1) return false;
		this.level += 1;
		this.over = 0;
		return true;
	}

	pass(tool: string, ms: number): boolean {
		if (!this.steppedDown || this.slowTools.has(tool)) return false;
		const runs = ms > SLOW_PASS_MS ? (this.slowRuns.get(tool) ?? 0) + 1 : 0;
		this.slowRuns.set(tool, runs);
		if (runs < this.window) return false;
		this.slowTools.add(tool);
		return true;
	}
}

export function driftMs(intervalMs: number, expectedMs: number): number {
	if (!(expectedMs > 0) || !(intervalMs > 0)) return 0;
	return intervalMs > expectedMs * 1.5 ? FRAME_BUDGET_MS + intervalMs - expectedMs : 0;
}
