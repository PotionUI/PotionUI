import { describe, expect, it } from 'vitest';
import { keptRange, outputDuration, outputToSource, playbackRate, sourceToOutput, type TimeOp } from './timeMap';

const close = (value: number, expected: number) => expect(value).toBeCloseTo(expected, 6);

describe('source and output time', () => {
	it('is the identity with no timing steps', () => {
		close(sourceToOutput(3.12, 8, []), 3.12);
		close(outputToSource(3.12, 8, []), 3.12);
		close(outputDuration(8, []), 8);
	});

	it('maps through a trim', () => {
		const ops: TimeOp[] = [{ kind: 'trim', start: 1.2, end: 6.4 }];
		close(outputDuration(8, ops), 5.2);
		close(sourceToOutput(3.12, 8, ops), 1.92);
		close(outputToSource(1.92, 8, ops), 3.12);
		close(sourceToOutput(0.5, 8, ops), 0);
		close(sourceToOutput(7.5, 8, ops), 5.2);
	});

	it('maps through a speed change', () => {
		const ops: TimeOp[] = [{ kind: 'speed', speed: 2 }];
		close(outputDuration(8, ops), 4);
		close(sourceToOutput(3, 8, ops), 1.5);
		close(outputToSource(1.5, 8, ops), 3);
	});

	it('maps through trim then speed and back', () => {
		const ops: TimeOp[] = [
			{ kind: 'trim', start: 1, end: 7 },
			{ kind: 'speed', speed: 0.5 }
		];
		close(outputDuration(8, ops), 12);
		close(sourceToOutput(4, 8, ops), 6);
		close(outputToSource(6, 8, ops), 4);
		expect(keptRange(8, ops)).toEqual({ start: 1, end: 7 });
	});

	it('maps through speed then a trim written in sped-up time', () => {
		const ops: TimeOp[] = [
			{ kind: 'speed', speed: 2 },
			{ kind: 'trim', start: 1, end: 3 }
		];
		close(outputDuration(8, ops), 2);
		close(sourceToOutput(4, 8, ops), 1);
		close(outputToSource(1, 8, ops), 4);
		const kept = keptRange(8, ops);
		close(kept.start, 2);
		close(kept.end, 6);
	});

	it('clamps a trim that runs past the clip and a speed out of range', () => {
		const ops: TimeOp[] = [
			{ kind: 'trim', start: -2, end: 20 },
			{ kind: 'speed', speed: 10 }
		];
		close(outputDuration(8, ops), 2);
		close(playbackRate(ops), 4);
		close(playbackRate([{ kind: 'speed', speed: 0 }]), 1);
	});
});
