import { describe, expect, it, vi } from 'vitest';
import { createEditorTasksApi, describeEditorTaskFailure, normalizeDetections, editorTaskFailureMessage, normalizeDefault, normalizeFormat, normalizeRun, normalizeTask } from './editorTasks';

function failure(status: number, detail: unknown) {
	return { response: { status, data: { detail } } };
}

describe('describeEditorTaskFailure', () => {
	it('maps each 422 code', () => {
		for (const code of ['task_not_offered', 'prompt_required', 'region_required', 'region_empty']) {
			expect(describeEditorTaskFailure(failure(422, { error: code }))).toEqual({ kind: code });
		}
	});

	it('says a setup the server found incomplete is missing a model, never the field names it sent', () => {
		const refused = failure(422, { error: 'form_validation_failed', message: 'vae: required field is missing', field_errors: { vae: ['required field is missing'] } });
		expect(describeEditorTaskFailure(refused)).toEqual({ kind: 'setup_incomplete' });
		expect(editorTaskFailureMessage({ kind: 'setup_incomplete' })).toBe('This setup is missing a model. Open Settings and choose it.');
	});

	it('maps plan limits, a refused cancel and missing things', () => {
		expect(describeEditorTaskFailure(failure(403, { error: 'limit_exceeded', message: 'Daily limit reached' }))).toEqual({ kind: 'limit', message: 'Daily limit reached' });
		expect(describeEditorTaskFailure(failure(400, { error: 'cancel_failed' }))).toEqual({ kind: 'cancel_failed' });
		expect(describeEditorTaskFailure(failure(404, { error: 'run_not_found' }))).toEqual({ kind: 'not_found', code: 'run_not_found' });
	});

	it('separates a lost connection and server errors from refusals', () => {
		expect(describeEditorTaskFailure({ message: 'Network Error' })).toEqual({ kind: 'network' });
		expect(describeEditorTaskFailure(failure(500, { error: 'run_start_failed' }))).toEqual({ kind: 'server', status: 500 });
		expect(describeEditorTaskFailure(failure(422, [{ msg: 'bad' }]))).toEqual({ kind: 'rejected', status: 422, code: null, message: null });
	});

	it('words every failure in plain terms without codes', () => {
		const kinds = [
			{ kind: 'task_not_offered' },
			{ kind: 'prompt_required' },
			{ kind: 'region_empty' },
			{ kind: 'setup_incomplete' },
			{ kind: 'limit', message: null },
			{ kind: 'cancel_failed' },
			{ kind: 'not_found', code: null },
			{ kind: 'network' },
			{ kind: 'server', status: 500 },
			{ kind: 'rejected', status: 400, code: null, message: null }
		] as const;
		for (const entry of kinds) expect(editorTaskFailureMessage(entry)).not.toMatch(/\d{3}|_/);
	});
});

describe('normalizers', () => {
	it('reads a run and clamps its progress', () => {
		const run = normalizeRun({ id: 'er_1', task: 'edit_region', status: 'running', progress: 40, rect: { x: 0.1, y: 0.2, width: 0.3, height: 0.4 }, candidates: [{ patch: { item_id: 'p1' }, url: '/a.png', seed: 5 }, { nope: true }] });
		expect(run.progress).toBeCloseTo(0.4);
		expect(run.candidates).toHaveLength(1);
		expect(run.rect?.width).toBe(0.3);
		expect(normalizeRun({ id: 'x', status: 'weird' }).status).toBe('failed');
	});

	it('reads a task with offers and no default', () => {
		const task = normalizeTask({ id: 'edit_region', label: 'Edit a region', offers: [{ preset_id: 'p', mode: 'editor:edit', from_mode: 'edit', short_description: 'Changes a part', order: 2, qualifies: false, reason: 'models_missing', owned: ['x'], variant: 'v' }, { junk: 1 }], default: null });
		expect(task?.offers).toHaveLength(1);
		expect(task?.offers[0]).toMatchObject({ mode: 'editor:edit', from_mode: 'edit', short_description: 'Changes a part', order: 2, qualifies: false, reason: 'models_missing', region_strategy: 'crop' });
		expect(task?.offers[0]).not.toHaveProperty('owned');
		expect(task?.offers[0]).not.toHaveProperty('variant');
		expect(task?.default).toBeNull();
		expect(normalizeTask({ label: 'no id' })).toBeNull();
	});

	it('reads a default with its health', () => {
		const value = normalizeDefault({ preset_id: 'p', mode: 'editor:edit', variant: 'old', setup: { kind: 'session', id: 's1' }, health: { ok: false, reason: 'session_deleted', name: 'Soft portrait', mode_label: 'Edit' } });
		expect(value).toMatchObject({ mode: 'editor:edit', setup: { kind: 'session', id: 's1' }, health: { ok: false, reason: 'session_deleted', name: 'Soft portrait' } });
		expect(value).not.toHaveProperty('variant');
		expect(normalizeDefault({ preset_id: 'p', mode: 'editor:edit' })?.health.ok).toBe(true);
		expect(normalizeDefault(null)).toBeNull();
	});
});

describe('createEditorTasksApi', () => {
	function fake() {
		return {
			get: vi.fn(async () => ({ data: { success: true, data: { tasks: [{ id: 'edit_region', label: 'Edit a region' }], run: { id: 'er_1', status: 'queued' } } } })),
			post: vi.fn(async () => ({ data: { success: true, data: { run: { id: 'er_2', status: 'queued' } } } })),
			put: vi.fn(async () => ({ data: { success: true, data: { id: 'edit_region', label: 'x' } } })),
			delete: vi.fn(async () => ({ data: { success: true, data: { run: { id: 'er_1', status: 'cancelled' } } } }))
		};
	}

	it('calls the task and run routes', async () => {
		const client = fake();
		const api = createEditorTasksApi(client as never);
		expect((await api.listEditorTasks('image'))[0].id).toBe('edit_region');
		expect(client.get).toHaveBeenCalledWith('/api/editor/tasks', { params: { editor: 'image' } });
		const body = { edit: { id: 'ed', step_id: 's1' }, input: { item_id: 'i' }, steps: [], region: null, prompt: null, setup: { preset_id: 'p', mode: 'editor:m', form_data: {} }, candidates: 1, seed: null };
		expect((await api.startEditorRun('edit_region', body)).id).toBe('er_2');
		expect(client.post).toHaveBeenCalledWith('/api/editor/tasks/edit_region/runs', body);
		expect((await api.getEditorRun('er_1')).status).toBe('queued');
		expect((await api.cancelEditorRun('er_1')).status).toBe('cancelled');
		expect(client.delete).toHaveBeenCalledWith('/api/editor/runs/er_1');
	});

	it('sets and clears a default', async () => {
		const client = fake();
		const api = createEditorTasksApi(client as never);
		await api.setEditorTaskDefault('edit_region', { preset_id: 'p', mode: 'editor:m', setup: { kind: 'defaults' } });
		expect(client.put).toHaveBeenCalledWith('/api/editor/tasks/edit_region/default', { preset_id: 'p', mode: 'editor:m', setup: { kind: 'defaults' } });
		await api.clearEditorTaskDefault('edit_region');
		expect(client.delete).toHaveBeenCalledWith('/api/editor/tasks/edit_region/default');
	});

	it('asks for editor sessions only', async () => {
		const client = fake();
		client.get.mockResolvedValueOnce({ data: { success: true, data: [{ id: 's1', name: 'Soft', data: {} }, { junk: 1 }] } } as never);
		const api = createEditorTasksApi(client as never);
		expect(await api.listEditorSessions('p')).toHaveLength(1);
		expect(client.get).toHaveBeenCalledWith('/api/sessions/preset/p', { params: { kind: 'editor' } });
	});
});

describe('default from the server', () => {
	it('reads the name from the setup and the stored names of a broken default', () => {
		const ok = normalizeDefault({ preset_id: 'p', preset_name: 'Qwen', mode: 'editor:edit', mode_label: 'Edit', variant: null, setup: { kind: 'session', id: 's', name: 'Soft', version: 3, updated_at: 'x' }, set_at: 'y', health: { ok: true } });
		expect(ok).toMatchObject({ setup: { kind: 'session', id: 's' }, health: { ok: true, name: 'Soft', mode_label: 'Edit' } });
		const broken = normalizeDefault({ preset_id: 'p', mode: 'edit', setup: { kind: 'defaults', id: null, name: null }, health: { ok: false, reason: 'preset_missing', missing: { kind: 'preset', id: 'p' }, name: 'Qwen edit', mode_label: 'Edit' } });
		expect(broken).toMatchObject({ setup: { kind: 'defaults' }, health: { ok: false, reason: 'preset_missing', name: 'Qwen edit' } });
	});
});

describe('the expand task and its formats', () => {
	it('reads the grows flag and leaves it off for other tasks', () => {
		expect(normalizeTask({ id: 'expand', label: 'Expand', grows: true, offers: [] })?.grows).toBe(true);
		expect(normalizeTask({ id: 'edit_region', label: 'Edit', offers: [] })).not.toHaveProperty('grows');
	});

	it('reads a format and refuses a broken one', () => {
		expect(normalizeFormat({ id: 'story', label: 'Story', ratio: '9:16', source: 'built_in', group_id: null })).toEqual({ id: 'story', label: 'Story', ratio: '9:16', source: 'built_in', group_id: null });
		expect(normalizeFormat({ id: 'house.t', label: 'T', ratio: '5:2', source: 'house', group_id: 'g' })?.source).toBe('house');
		expect(normalizeFormat({ id: 'x', label: 'X' })).toBeNull();
	});

	it('asks the server for the formats', async () => {
		const get = vi.fn(async () => ({ data: { success: true, data: { formats: [{ id: 'story', label: 'Story', ratio: '9:16', source: 'built_in', group_id: null }, { nope: true }] } } }));
		const api = createEditorTasksApi({ get } as never);

		const formats = await api.listExpandFormats();

		expect(get).toHaveBeenCalledWith('/api/editor/expand/formats');
		expect(formats.map((format) => format.id)).toEqual(['story']);
	});
});

describe('upscale runs', () => {
	it('reads the parts and the size of a run and of each candidate', () => {
		const run = normalizeRun({ id: 'er', status: 'running', progress: 0.4, parts: { done: 2, total: 6 }, size: { width: 4096, height: 3072 }, candidates: [{ patch: { item_id: 'p' }, url: '/p.png', seed: 1, size: { width: 4096, height: 3072 } }] });
		expect(run.parts).toEqual({ done: 2, total: 6 });
		expect(run.size).toEqual({ width: 4096, height: 3072 });
		expect(run.candidates[0].size).toEqual({ width: 4096, height: 3072 });
	});

	it('keeps old runs as they were', () => {
		const run = normalizeRun({ id: 'er', status: 'done', candidates: [{ patch: { item_id: 'p' }, url: '/p.png', seed: 1 }] });
		expect(run.parts).toBeNull();
		expect(run.size).toBeNull();
		expect(run.candidates[0]).not.toHaveProperty('size');
	});

	it('bounds parts that make no sense', () => {
		expect(normalizeRun({ id: 'er', status: 'running', parts: { done: 9, total: 4 } }).parts).toEqual({ done: 4, total: 4 });
		expect(normalizeRun({ id: 'er', status: 'running', parts: { done: 0, total: 0 } }).parts).toBeNull();
	});

	it('words the two size refusals', () => {
		expect(describeEditorTaskFailure(failure(422, { error: 'upscale_not_larger' }))).toEqual({ kind: 'upscale_not_larger' });
		expect(describeEditorTaskFailure(failure(422, { error: 'upscale_too_large' }))).toEqual({ kind: 'upscale_too_large' });
		expect(editorTaskFailureMessage({ kind: 'upscale_not_larger' })).toBe('Pick a size larger than the picture.');
	});
});

describe('faces and hands', () => {
	it('reads detections and drops what has no box or kind', () => {
		const result = normalizeDetections({
			picture: { width: 640, height: 480 },
			detections: [
				{ id: 'd1', kind: 'face', box: { x: 0.1, y: 0.1, width: 0.2, height: 0.3 }, score: 0.9 },
				{ id: 'd2', kind: 'hand', box: { x: 0.5, y: 0.5, width: 0.1, height: 0.1 } },
				{ id: 'd3', kind: 'tail', box: { x: 0, y: 0, width: 1, height: 1 }, score: 1 },
				{ id: 'd4', kind: 'face', box: { x: 0, y: 0, width: 0, height: 1 }, score: 1 },
				{ kind: 'face', box: { x: 0, y: 0, width: 1, height: 1 } }
			]
		});
		expect(result.picture).toEqual({ width: 640, height: 480 });
		expect(result.detections.map((item) => [item.id, item.kind, item.score])).toEqual([['d1', 'face', 0.9], ['d2', 'hand', 0]]);
	});

	it('posts the picture source and the steps to the detections route', async () => {
		const post = vi.fn(async () => ({ data: { success: true, data: { picture: { width: 10, height: 10 }, detections: [] } } }));
		const api = createEditorTasksApi({ post } as never);
		const result = await api.detectEditorTask('fix_faces', { input: { item_id: 'p' }, steps: [] });
		expect(post).toHaveBeenCalledWith('/api/editor/tasks/fix_faces/detections', { input: { item_id: 'p' }, steps: [] });
		expect(result.detections).toEqual([]);
	});

	it('tells an absent detector from a server failure', () => {
		expect(describeEditorTaskFailure(failure(503, { error: 'detector_unavailable' }))).toEqual({ kind: 'detector_unavailable' });
		expect(describeEditorTaskFailure(failure(503, { error: 'other' }))).toEqual({ kind: 'server', status: 503 });
	});

	it('reads the strength of an offer and ignores a broken one', () => {
		const offer = (strength: unknown) => normalizeTask({ id: 'fix_faces', offers: [{ preset_id: 'p', mode: 'm', strength }] })!.offers[0].strength;
		expect(offer({ default: 0.35, min: 0.1, max: 0.8 })).toEqual({ default: 0.35, min: 0.1, max: 0.8 });
		expect(offer({ default: 5, min: 0.1, max: 0.8 })).toEqual({ default: 0.8, min: 0.1, max: 0.8 });
		expect(offer({ default: 0.3, min: 0.9, max: 0.1 })).toBeNull();
		expect(offer(undefined)).toBeNull();
	});
});
