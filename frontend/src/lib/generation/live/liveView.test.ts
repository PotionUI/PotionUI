import { describe, expect, it } from 'vitest';
import { batchSlots, batchTotal, elapsedSeconds, formatElapsed, liveStepLine, queuedSeconds, tileStepLabel } from './liveView';
import { liveParameters, SEED_PENDING_COPY } from './liveParams';
import { initialLive } from './liveState';

describe('liveView', () => {
	it('formats elapsed as a clock', () => {
		expect(formatElapsed(23.9)).toBe('00:23');
		expect(formatElapsed(72)).toBe('01:12');
		expect(formatElapsed(3725)).toBe('1:02:05');
		expect(formatElapsed(null)).toBe('00:00');
	});

	it('measures from the start and reads queue time', () => {
		const g = { created_at: '2026-11-18T14:02:00Z', started_at: '2026-11-18T14:02:03Z', queued_seconds: null };
		expect(elapsedSeconds(g, Date.parse('2026-11-18T14:02:26Z'))).toBe(23);
		expect(queuedSeconds(g)).toBe(3);
		expect(queuedSeconds({ ...g, queued_seconds: 5 })).toBe(5);
	});

	it('builds the step line and the tile label from the step template', () => {
		const live = { ...initialLive('g'), pipeName: 'generator', currentStep: '<<PIPE:generator:bolt>> <<PROGRESS:17/28>>', progress: 0.4, itemIndex: 1, itemCount: 4 };
		expect(liveStepLine(live, { isAdmin: false })).toMatchObject({ text: 'Generating image 2 of 4 · step 17/28', percent: 40 });
		expect(tileStepLabel(live)).toBe('Generating · 17/28');
		expect(liveStepLine(live, { isAdmin: false, queued: true }).text).toBe('Waiting in the queue');
	});

	it('sizes the batch from the status or the form and marks the active slot', () => {
		expect(batchTotal({ quantity: 4 }, null)).toBe(4);
		expect(batchTotal({}, 3)).toBe(3);
		expect(batchTotal({}, null)).toBe(1);
		expect(batchSlots(4, 1, null).map((s) => s.state)).toEqual(['done', 'active', 'waiting', 'waiting']);
		expect(batchSlots(4, 1, 'failed').map((s) => s.state)).toEqual(['done', 'failed', 'waiting', 'waiting']);
	});
});

describe('liveParameters', () => {
	it('builds rows from form_data and states when the seed is chosen', () => {
		const form = { prompt: 'a dome', steps: 28, seed: -1, lora: { a: 1 }, empty: '' };
		const { params } = liveParameters(form, null);
		expect(params).toMatchObject({ prompt: 'a dome', steps: 28, seed: SEED_PENDING_COPY });
		expect(params).not.toHaveProperty('lora');
		expect(params).not.toHaveProperty('empty');
		expect(liveParameters(form, 4211984).params.seed).toBe(4211984);
		expect(liveParameters({ seed: 7 }, null).params.seed).toBe(7);
	});
});
