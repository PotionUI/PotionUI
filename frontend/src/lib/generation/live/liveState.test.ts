import { describe, expect, it } from 'vitest';
import { applyLiveMessage, initialLive } from './liveState';

const CLIP = {
	type: 'workbench_preview_clip',
	image: 'data:image/webp;base64,AAAA',
	frames: 4,
	columns: 2,
	frame_width: 64,
	frame_height: 64,
	fps: 8,
	step: 4,
	total_steps: 28,
	source_indices: [0, 1, 2, 3]
};

describe('applyLiveMessage', () => {
	it('keeps progress and the step template from a status message', () => {
		const next = applyLiveMessage(initialLive('g'), {
			type: 'generation_status',
			status: 'running',
			progress: 0.4,
			current_step: '<<PIPE:generator:bolt>> <<PROGRESS:17/28>>',
			item_index: 1,
			item_count: 4
		});
		expect(next).toMatchObject({ progress: 0.4, itemIndex: 1, itemCount: 4, status: 'running' });
		expect(next.currentStep).toContain('17/28');
	});

	it('takes a replayed preview only while none is held', () => {
		const first = applyLiveMessage(initialLive('g'), { type: 'workbench_update', image: 'data:image/png;base64,AA', replayed: true });
		expect(first.preview).toBe('data:image/png;base64,AA');
		const live = applyLiveMessage(first, { type: 'workbench_update', image: 'data:image/png;base64,BB' });
		const stale = applyLiveMessage(live, { type: 'workbench_update', image: 'data:image/png;base64,CC', replayed: true });
		expect(stale.preview).toBe('data:image/png;base64,BB');
	});

	it('parses a preview clip and ignores a malformed one', () => {
		expect(applyLiveMessage(initialLive('g'), CLIP).clip?.frames).toBe(4);
		const state = initialLive('g');
		expect(applyLiveMessage(state, { ...CLIP, frames: 0 })).toBe(state);
	});

	it('drops pictures when the server suppresses the preview', () => {
		const held = applyLiveMessage(initialLive('g'), { type: 'workbench_update', image: 'data:image/png;base64,AA' });
		const next = applyLiveMessage(held, { type: 'workbench_update', preview_suppressed: true });
		expect(next.preview).toBeNull();
		expect(next.previewSuppressed).toBe(true);
	});

	it('counts gallery updates and reads the seed artifact', () => {
		let state = applyLiveMessage(initialLive('g'), { type: 'gallery_update' });
		state = applyLiveMessage(state, { type: 'pipe_artifact', artifact_type: 'seed', artifact_data: { seed: 42 } });
		expect(state).toMatchObject({ galleryUpdates: 1, seed: 42 });
	});

	it('settles on complete, error and cancel and then ignores late frames', () => {
		const failed = applyLiveMessage(initialLive('g'), { type: 'generation_error', message: 'boom' });
		expect(failed).toMatchObject({ status: 'failed', error: 'boom' });
		expect(applyLiveMessage(failed, { type: 'generation_status', status: 'running', progress: 0.5 })).toBe(failed);
		expect(applyLiveMessage(initialLive('g'), { type: 'generation_complete' }).status).toBe('completed');
		expect(applyLiveMessage(initialLive('g'), { type: 'generation_cancelled' }).status).toBe('cancelled');
	});
});
