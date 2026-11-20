export type LoopStep = 'continue' | 'wrap' | 'stop';

export function loopStep(input: { time: number; rangeEnd: number; loop: boolean; ended: boolean }): LoopStep {
	if (!input.ended && input.time < input.rangeEnd - 0.005) return 'continue';
	return input.loop ? 'wrap' : 'stop';
}
