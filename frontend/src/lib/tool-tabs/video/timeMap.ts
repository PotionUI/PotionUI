export type TimeOp = { kind: 'trim'; start: number; end: number } | { kind: 'speed'; speed: number };

export const MIN_SPEED = 0.25;
export const MAX_SPEED = 4;

function clamp(value: number, low: number, high: number): number {
	if (!Number.isFinite(value)) return low;
	return Math.min(high, Math.max(low, value));
}

export function safeSpeed(speed: number): number {
	if (!Number.isFinite(speed) || speed <= 0) return 1;
	return clamp(speed, MIN_SPEED, MAX_SPEED);
}

function safeLength(duration: number): number {
	return Number.isFinite(duration) && duration > 0 ? duration : 0;
}

type Stage = { kind: 'trim'; start: number } | { kind: 'speed'; speed: number };

function stages(duration: number, ops: readonly TimeOp[]): { stages: Stage[]; length: number } {
	let length = safeLength(duration);
	const out: Stage[] = [];
	for (const op of ops) {
		if (op.kind === 'trim') {
			const start = clamp(op.start, 0, length);
			const end = clamp(op.end, start, length);
			out.push({ kind: 'trim', start });
			length = end - start;
		} else {
			const speed = safeSpeed(op.speed);
			out.push({ kind: 'speed', speed });
			length = length / speed;
		}
	}
	return { stages: out, length };
}

export function outputDuration(duration: number, ops: readonly TimeOp[]): number {
	return stages(duration, ops).length;
}

export function sourceToOutput(seconds: number, duration: number, ops: readonly TimeOp[]): number {
	let length = safeLength(duration);
	let t = clamp(seconds, 0, length);
	for (const op of ops) {
		if (op.kind === 'trim') {
			const start = clamp(op.start, 0, length);
			const end = clamp(op.end, start, length);
			t = clamp(t, start, end) - start;
			length = end - start;
		} else {
			const speed = safeSpeed(op.speed);
			t = t / speed;
			length = length / speed;
		}
	}
	return t;
}

export function outputToSource(seconds: number, duration: number, ops: readonly TimeOp[]): number {
	const plan = stages(duration, ops);
	let t = clamp(seconds, 0, plan.length);
	for (let i = plan.stages.length - 1; i >= 0; i -= 1) {
		const stage = plan.stages[i];
		t = stage.kind === 'trim' ? t + stage.start : t * stage.speed;
	}
	return t;
}

export function keptRange(duration: number, ops: readonly TimeOp[]): { start: number; end: number } {
	const length = outputDuration(duration, ops);
	return { start: outputToSource(0, duration, ops), end: outputToSource(length, duration, ops) };
}

export function playbackRate(ops: readonly TimeOp[]): number {
	return ops.reduce((rate, op) => (op.kind === 'speed' ? rate * safeSpeed(op.speed) : rate), 1);
}
