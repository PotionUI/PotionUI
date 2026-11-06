import { describe, it, expect, vi } from 'vitest';
import type { AxiosInstance } from 'axios';

vi.mock('$lib/services/api/index', () => ({ api: { getClient: () => ({}) } }));

import { createMediaToolsApi } from './mediaTools';

function client(overrides: Record<string, unknown>) {
	return overrides as unknown as AxiosInstance;
}

describe('listMediaTools', () => {
	it('sends the kind and scope as query params and unwraps the envelope', async () => {
		const get = vi.fn().mockResolvedValue({
			data: { success: true, data: { tools: [{ id: 'trim' }, { id: 'speed' }] } }
		});
		const api = createMediaToolsApi(client({ get }));

		const tools = await api.listMediaTools('video', 'library');

		expect(get).toHaveBeenCalledWith('/api/media-tools', {
			params: { kind: 'video', scope: 'library' }
		});
		expect(tools.map((tool) => tool.id)).toEqual(['trim', 'speed']);
	});

	it('omits params it was not given', async () => {
		const get = vi.fn().mockResolvedValue({ data: { success: true, data: { tools: [] } } });
		await createMediaToolsApi(client({ get })).listMediaTools();
		expect(get).toHaveBeenCalledWith('/api/media-tools', { params: {} });
	});

	it('throws the server message when the envelope reports a failure', async () => {
		const get = vi.fn().mockResolvedValue({ data: { success: false, message: 'Not today' } });
		await expect(createMediaToolsApi(client({ get })).listMediaTools()).rejects.toThrow('Not today');
	});
});

describe('startMediaToolRun', () => {
	it('posts the body as given and returns the run id', async () => {
		const post = vi.fn().mockResolvedValue({ data: { success: true, data: { run_id: 'run-1' } } });
		const body = {
			steps: [{ tool: 'speed', params: { speed: 2, keep_pitch: true } }],
			inputs: [{ item_id: 'row-1' }],
			output: { mode: 'new' as const }
		};

		const result = await createMediaToolsApi(client({ post })).startMediaToolRun(body);

		expect(post).toHaveBeenCalledWith('/api/media-tools/runs', body);
		expect(result).toEqual({ run_id: 'run-1' });
	});

	it('lets a refused request reject with the response intact', async () => {
		const refusal = { response: { status: 400, data: { detail: { error: 'invalid_params' } } } };
		const post = vi.fn().mockRejectedValue(refusal);
		await expect(
			createMediaToolsApi(client({ post })).startMediaToolRun({
				steps: [],
				inputs: [],
				output: { mode: 'new' }
			})
		).rejects.toBe(refusal);
	});
});

describe('getMediaToolRun', () => {
	it('encodes the run id and forwards the abort signal', async () => {
		const run = { run_id: 'a/b', status: 'running', progress: 0.5, label: '', items: [], replaced: false, error: null };
		const get = vi.fn().mockResolvedValue({ data: { success: true, data: run } });
		const controller = new AbortController();

		const result = await createMediaToolsApi(client({ get })).getMediaToolRun('a/b', controller.signal);

		expect(get).toHaveBeenCalledWith('/api/media-tools/runs/a%2Fb', { signal: controller.signal });
		expect(result).toEqual(run);
	});
});

describe('cancelMediaToolRun', () => {
	it('deletes the run and returns the snapshot the server answers with, which may already be final', async () => {
		const snapshot = { run_id: 'run-9', status: 'completed', progress: 1, label: '', items: [{ id: 'r' }], replaced: true, error: null };
		const del = vi.fn().mockResolvedValue({ data: { success: true, data: snapshot } });
		const result = await createMediaToolsApi(client({ delete: del })).cancelMediaToolRun('run-9');
		expect(del).toHaveBeenCalledWith('/api/media-tools/runs/run-9');
		expect(result).toEqual(snapshot);
	});

	it('returns null when the answer carries no run', async () => {
		const del = vi.fn().mockResolvedValue({ data: { success: true } });
		expect(await createMediaToolsApi(client({ delete: del })).cancelMediaToolRun('run-9')).toBeNull();
	});

	it('encodes the run id', async () => {
		const del = vi.fn().mockResolvedValue({ data: { success: true } });
		await createMediaToolsApi(client({ delete: del })).cancelMediaToolRun('a/b');
		expect(del).toHaveBeenCalledWith('/api/media-tools/runs/a%2Fb');
	});
});

describe('previewMediaTool', () => {
	it('asks for a blob and returns the bytes', async () => {
		const blob = new Blob(['x'], { type: 'image/webp' });
		const post = vi.fn().mockResolvedValue({ data: blob });
		const body = { tool: 'rotate', params: { degrees: 90 }, input: { item_id: 'row-1' }, time_seconds: 1.5 };

		const result = await createMediaToolsApi(client({ post })).previewMediaTool(body);

		expect(post).toHaveBeenCalledWith('/api/media-tools/preview', body, {
			responseType: 'blob',
			signal: undefined
		});
		expect(result).toBe(blob);
	});
});

describe('editor endpoints', () => {
	it('lists my runs from the envelope', async () => {
		const get = vi.fn().mockResolvedValue({
			data: { success: true, data: { runs: [{ run_id: 'r1', kind: 'preview' }] } }
		});
		const runs = await createMediaToolsApi(client({ get })).listMediaToolRuns();
		expect(get).toHaveBeenCalledWith('/api/media-tools/runs');
		expect(runs.map((run) => run.run_id)).toEqual(['r1']);
	});

	it('reads a frame preview as a blob and reports a cache hit', async () => {
		const blob = new Blob(['x']);
		const post = vi.fn().mockResolvedValue({ data: blob, headers: { 'x-preview-cache': 'hit' } });
		const body = { input: { item_id: 'v1' }, steps: [{ tool: 'film_grain', params: {}, hidden: true }] };

		const result = await createMediaToolsApi(client({ post })).previewFrame(body);

		expect(post).toHaveBeenCalledWith('/api/media-tools/preview/frame', body, {
			responseType: 'blob',
			signal: undefined
		});
		expect(result).toEqual({ blob, cached: true });
	});

	it('returns a cached clip without a run id', async () => {
		const preview = { id: 'p', url: '/api/media-tools/previews/p', mime_type: 'video/mp4' };
		const post = vi.fn().mockResolvedValue({ data: { success: true, data: { run_id: null, preview } } });
		const started = await createMediaToolsApi(client({ post })).startClipPreview({
			input: { item_id: 'v1' },
			steps: []
		});
		expect(post).toHaveBeenCalledWith('/api/media-tools/preview/clip', { input: { item_id: 'v1' }, steps: [] });
		expect(started).toEqual({ run_id: null, preview });
	});

	it('uploads a helper file as form data and returns its id', async () => {
		const post = vi.fn().mockResolvedValue({ data: { success: true, data: { id: 'a1', media_type: 'image' } } });
		const file = new File(['png'], 'layer.png', { type: 'image/png' });

		const artifact = await createMediaToolsApi(client({ post })).uploadMediaToolArtifact(file);

		const [url, form] = post.mock.calls[0];
		expect(url).toBe('/api/media-tools/artifacts');
		expect((form as FormData).get('file')).toBeInstanceOf(File);
		expect(artifact.id).toBe('a1');
	});
});
