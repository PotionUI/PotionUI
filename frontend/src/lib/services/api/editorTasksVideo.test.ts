import { describe, expect, it, vi } from 'vitest';
import { createEditorTasksApi, describeEditorTaskFailure, editorTaskFailureMessage, normalizeRun } from './editorTasks';

function failure(status: number, detail: unknown) {
	return { response: { status, data: { detail } } };
}

describe('video tasks and runs', () => {
	it('lists the video tasks with what each needs', async () => {
		const client = { get: vi.fn(async () => ({ data: { success: true, data: { tasks: [{ id: 'extend', label: 'Extend', icon: 'mode-extend', editors: ['video'], region: 'none', prompt: 'optional', range: 'optional', result: 'append', audio: 'result', resizes: false, offers: [], default: null }] } } })) };
		const api = createEditorTasksApi(client as never);
		const [extend] = await api.listEditorTasks('video');
		expect(client.get).toHaveBeenCalledWith('/api/editor/tasks', { params: { editor: 'video' } });
		expect(extend).toMatchObject({ id: 'extend', editors: ['video'], range: 'optional', result: 'append', audio: 'result', resizes: false });
	});

	it('posts the range in seconds with the run', async () => {
		const client = { post: vi.fn(async () => ({ data: { success: true, data: { run: { id: 'er_1', status: 'queued' } } } })) };
		const api = createEditorTasksApi(client as never);
		const body = { edit: { id: 'video-tab', step_id: 's1' }, input: { item_id: 'clip' }, steps: [], region: null, range: { start_s: 2, end_s: 4 }, prompt: null, setup: { preset_id: 'ltx', mode: 'editor:restore', form_data: {} }, candidates: 2, seed: null };
		await api.startEditorRun('restore', body);
		expect(client.post).toHaveBeenCalledWith('/api/editor/tasks/restore/runs', body);
	});

	it('reads a settled run: the range it ran on and a clip per candidate, with where an extension was added', () => {
		const run = normalizeRun({
			id: 'er_1',
			task: 'extend',
			status: 'done',
			range: { start_s: 0, end_s: 4, whole: false },
			candidates: [
				{ clip: { item_id: 'c1' }, url: '/u/c1.mp4', seed: 3, generation_file_id: 'g1', range: { start_s: 0, end_s: 4 }, added: { at_s: 4, seconds: 5 }, duration_s: 11.4 },
				{ clip: { item_id: 'c2' }, seed: 4 },
				{ patch: { item_id: 'p1' }, url: '/p1.png', seed: 5 }
			]
		});
		expect(run.range).toEqual({ start_s: 0, end_s: 4, whole: false });
		expect(run.candidates).toHaveLength(2);
		expect(run.candidates[0]).toMatchObject({ patch: { item_id: 'c1' }, clip: true, url: '/u/c1.mp4', range: { start_s: 0, end_s: 4 }, added: { at_s: 4, seconds: 5 }, duration_s: 11.4 });
		expect(run.candidates[1].clip).toBeUndefined();
	});

	it('words a refused range', () => {
		const refused = failure(422, { error: 'range_outside_clip', message: 'The range ends (9s) past the end of the clip (6.400s)' });
		expect(describeEditorTaskFailure(refused)).toEqual({ kind: 'range_invalid', message: 'The range ends (9s) past the end of the clip (6.400s)' });
		expect(editorTaskFailureMessage({ kind: 'range_invalid', message: null })).toBe('Mark the part of the clip again.');
	});
});
