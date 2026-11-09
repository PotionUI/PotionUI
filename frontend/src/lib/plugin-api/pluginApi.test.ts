// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { AxiosError } from 'axios';
import { Blob as NodeBlob } from 'node:buffer';
import { APIClient } from '$lib/services/api/client';
import { createHostFetchApi } from './hostApi';
import { createPluginApi, PluginApiError } from './pluginApi';

const auth = vi.hoisted(() => ({ token: 'tok' as string | null }));

vi.stubGlobal('Blob', NodeBlob);

vi.mock('$lib/utils/storage', () => ({
	storage: { get: () => auth.token, set: () => undefined, remove: () => undefined }
}));

interface Call {
	url: string;
	method: string;
	headers: Record<string, unknown>;
	data: unknown;
	timeout: number | undefined;
	withCredentials: boolean | undefined;
}

interface Reply {
	status?: number;
	body?: BlobPart | null;
	headers?: Record<string, string>;
}

function pluginApiHarness(pluginId: string, respond: (call: Call) => Reply | Error, token: string | null = 'tok') {
	auth.token = token;
	const calls: Call[] = [];
	const client = new APIClient('');
	client.getClient().defaults.adapter = async (config) => {
		const call: Call = {
			url: config.url ?? '',
			method: (config.method ?? 'get').toUpperCase(),
			headers: config.headers.toJSON() as Record<string, unknown>,
			data: config.data,
			timeout: config.timeout,
			withCredentials: config.withCredentials
		};
		calls.push(call);
		const reply = respond(call);
		if (reply instanceof Error) throw reply;
		return {
			data: new Blob(reply.body == null ? [] : [reply.body]),
			status: reply.status ?? 200,
			statusText: '',
			headers: reply.headers ?? {},
			config
		};
	};
	const expired = vi.fn();
	client.setOnAuthExpired(expired);
	const host = createHostFetchApi(client);
	return { api: createPluginApi(pluginId, host, () => client.getToken()), calls, expired, client };
}

function setup(respond: (call: Call) => Reply | Error, token: string | null = 'tok') {
	return pluginApiHarness('my-plugin', respond, token);
}

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Reply {
	return { status, body: JSON.stringify(body), headers: { 'content-type': 'application/json', ...headers } };
}

function headerOf(call: Call, name: string): unknown {
	const key = Object.keys(call.headers).find((k) => k.toLowerCase() === name.toLowerCase());
	return key === undefined ? null : call.headers[key];
}

describe('createPluginApi: the methods model-browser relies on', () => {
	it('sends JSON with the bearer token to the plugin routes', async () => {
		const { api, calls } = setup(() => json({ success: true, data: 1 }));

		await api.get('/projects');
		await api.post('/projects', { name: 'Teaser' });
		await api.put('/projects/1', { name: 'Trailer' });
		await api.delete('/projects/1');

		expect(calls.map((call) => [call.method, call.url])).toEqual([
			['GET', '/api/plugins/my-plugin/projects'],
			['POST', '/api/plugins/my-plugin/projects'],
			['PUT', '/api/plugins/my-plugin/projects/1'],
			['DELETE', '/api/plugins/my-plugin/projects/1']
		]);
		for (const call of calls) {
			expect(call.withCredentials).toBe(true);
			expect(headerOf(call, 'Authorization')).toBe('Bearer tok');
		}
		expect(calls[1].data).toBe('{"name":"Teaser"}');
		expect(headerOf(calls[1], 'Content-Type')).toBe('application/json');
	});

	it('still resolves with the parsed body of an error response', async () => {
		const { api } = setup(() => json({ detail: { success: false, error: 'not_found', message: 'Gone' } }, 404));

		await expect(api.get('/models/1')).resolves.toEqual({
			detail: { success: false, error: 'not_found', message: 'Gone' }
		});
	});

	it('resolves null when the body is not JSON', async () => {
		const { api } = setup(() => ({ status: 502, body: '<html>Bad gateway</html>' }));

		await expect(api.get('/models')).resolves.toBeNull();
	});

	it('uploads form data as multipart and reads core routes through fetchFromApp', async () => {
		const { api, calls } = setup(() => json({ success: true }));
		const form = new FormData();

		await api.uploadFile('/media', form);
		await api.fetchFromApp('/api/backends');

		expect(calls[0]).toMatchObject({ method: 'POST', data: form, timeout: 0 });
		expect(headerOf(calls[0], 'Content-Type')).toBe('multipart/form-data');
		expect(headerOf(calls[0], 'Authorization')).toBe('Bearer tok');
		expect(calls[1].url).toBe('/api/backends');
	});

	it('exposes the token and omits the header when there is none', async () => {
		const { api, calls } = setup(() => json({}), null);

		expect(api.getToken()).toBeNull();
		await api.get('/me');
		expect(headerOf(calls[0], 'Authorization')).toBeNull();
	});
});

describe('createPluginApi: request and patch', () => {
	it('returns the parsed JSON body of a success', async () => {
		const { api, calls } = setup(() => json({ success: true, data: { id: 3 } }));

		await expect(api.request('get', '/shots/3')).resolves.toEqual({ success: true, data: { id: 3 } });
		expect(calls[0].method).toBe('GET');
		expect(calls[0].data).toBeUndefined();
		expect(headerOf(calls[0], 'Authorization')).toBe('Bearer tok');
	});

	it('patches with a JSON body and no client timeout', async () => {
		const { api, calls } = setup(() => json({ success: true }));

		await api.patch('/shots/3', { duration: 4 });

		expect(calls[0].url).toBe('/api/plugins/my-plugin/shots/3');
		expect(calls[0].method).toBe('PATCH');
		expect(calls[0].data).toBe('{"duration":4}');
		expect(headerOf(calls[0], 'Content-Type')).toBe('application/json');
		expect(calls[0].withCredentials).toBe(true);
		expect(calls[0].timeout).toBe(0);
	});

	it('returns null for 204 No Content', async () => {
		const { api } = setup(() => ({ status: 204 }));

		await expect(api.request('DELETE', '/shots/3')).resolves.toBeNull();
	});

	it('throws the status, the body and the detail of a 4xx', async () => {
		const body = { detail: 'Shot 3 is locked' };
		const { api } = setup(() => json(body, 409));

		const error = await api.patch('/shots/3', {}).catch((err) => err);

		expect(error).toBeInstanceOf(PluginApiError);
		expect(error).toMatchObject({ status: 409, body, detail: 'Shot 3 is locked', message: 'Shot 3 is locked' });
	});

	it('takes the message of a structured detail', async () => {
		const detail = { error: 'limit_exceeded', message: 'Quota reached' };
		const { api } = setup(() => json({ detail }, 403));

		await expect(api.request('POST', '/renders', {})).rejects.toMatchObject({
			status: 403,
			detail,
			message: 'Quota reached'
		});
	});

	it('throws the status and the raw text of a 5xx that is not JSON', async () => {
		const { api } = setup(() => ({ status: 502, body: '<html>Bad gateway</html>' }));

		await expect(api.request('GET', '/renders')).rejects.toMatchObject({
			status: 502,
			body: '<html>Bad gateway</html>',
			detail: undefined,
			message: 'Request failed with status 502'
		});
	});

	it('rejects a network failure with a TypeError', async () => {
		const { api } = setup(() => new AxiosError('Network Error', 'ERR_NETWORK'));

		await expect(api.request('GET', '/renders')).rejects.toBeInstanceOf(TypeError);
	});

	it('ends the session once on a 401, with the token that was sent', async () => {
		const { api, expired } = setup(() => json({ detail: 'Not authenticated' }, 401));

		await expect(api.request('GET', '/me')).rejects.toMatchObject({ status: 401 });
		expect(expired).toHaveBeenCalledTimes(1);
		expect(expired).toHaveBeenCalledWith('tok');
	});

	it('adopts a refreshed token for the next call', async () => {
		let first = true;
		const { api, calls, client } = setup(() => {
			if (!first) return json({ success: true });
			first = false;
			return json({ success: true }, 200, { 'x-refreshed-token': 'tok-2' });
		});

		await api.request('GET', '/me');
		await api.request('GET', '/me');

		expect(client.getToken()).toBe('tok-2');
		expect(api.getToken()).toBe('tok-2');
		expect(headerOf(calls[1], 'Authorization')).toBe('Bearer tok-2');
	});
});

describe('createPluginApi: fetchRaw', () => {
	it('returns the response of a binary route with the bearer set', async () => {
		const bytes = new Uint8Array([0, 159, 146, 150]);
		const { api, calls } = setup(() => ({ status: 200, body: bytes, headers: { 'content-type': 'video/mp4' } }));

		const res = await api.fetchRaw('/renders/1/file', { headers: { Range: 'bytes=0-3' } });

		expect(new Uint8Array(await res.arrayBuffer())).toEqual(bytes);
		expect(res.headers.get('Content-Type')).toBe('video/mp4');
		expect(calls[0].url).toBe('/api/plugins/my-plugin/renders/1/file');
		expect(calls[0].withCredentials).toBe(true);
		expect(calls[0].timeout).toBe(0);
		expect(headerOf(calls[0], 'Authorization')).toBe('Bearer tok');
		expect(headerOf(calls[0], 'Range')).toBe('bytes=0-3');
	});

	it('hands back an error response instead of throwing', async () => {
		const { api } = setup(() => json({ detail: 'Not found' }, 404));

		const res = await api.fetchRaw('/renders/9/file');

		expect(res.status).toBe(404);
		expect(await res.json()).toEqual({ detail: 'Not found' });
	});

	it('sends a Blob with its own content type', async () => {
		const { api, calls } = setup(() => json({ success: true }));

		await api.fetchRaw('/frames', { method: 'POST', body: new Blob(['png'], { type: 'image/png' }) });

		expect(headerOf(calls[0], 'Content-Type')).toBe('image/png');
	});

	it('rejects with an AbortError when the signal aborts', async () => {
		const controller = new AbortController();
		controller.abort();
		const { api } = setup(() => json({}));

		await expect(api.fetchRaw('/estimate', { method: 'POST', signal: controller.signal })).rejects.toMatchObject({
			name: 'AbortError'
		});
	});

	it('reaches core routes through fetchRawFromApp and reports the upload progress', async () => {
		const progress: [number, number][] = [];
		const { api, calls, client } = setup(() => json({ success: true, data: { filename: 'a.mp4' } }));
		const adapter = client.getClient().defaults.adapter as (config: any) => Promise<any>;
		client.getClient().defaults.adapter = async (config) => {
			config.onUploadProgress?.({ loaded: 5, total: 20, bytes: 5, lengthComputable: true });
			return adapter(config);
		};
		const form = new FormData();

		const res = await api.fetchRawFromApp('/api/media/upload', {
			method: 'POST',
			body: form,
			onUploadProgress: (loaded, total) => progress.push([loaded, total])
		});

		expect(await res.json()).toEqual({ success: true, data: { filename: 'a.mp4' } });
		expect(calls[0]).toMatchObject({ url: '/api/media/upload', method: 'POST', data: form, timeout: 0 });
		expect(progress).toEqual([[5, 20]]);
	});

	it('refuses a path on another host so the token never leaves the app', async () => {
		const { api, calls } = setup(() => json({}));

		await expect(api.fetchRawFromApp(`https:${'/'.repeat(2)}evil.example/x`)).rejects.toBeInstanceOf(TypeError);
		expect(calls).toHaveLength(0);
	});
});

describe('createPluginApi: download', () => {
	let clicked: HTMLAnchorElement[];

	beforeEach(() => {
		clicked = [];
		Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:export-1') });
		Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
		vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
			clicked.push(this);
		});
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.restoreAllMocks();
	});

	it('saves the response as a file, then removes the link and revokes the object URL', async () => {
		const { api, calls } = setup(() => ({ status: 200, body: '{"tracks":[]}' }));

		await api.download('/projects/1/export.otio', 'teaser.otio');
		vi.useFakeTimers();

		expect(headerOf(calls[0], 'Authorization')).toBe('Bearer tok');
		expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
		expect(clicked).toHaveLength(1);
		expect(clicked[0].download).toBe('teaser.otio');
		expect(clicked[0].href).toBe('blob:export-1');
		expect(clicked[0].isConnected).toBe(false);
		expect(URL.revokeObjectURL).not.toHaveBeenCalled();
	});

	it('revokes the object URL a minute after the click', async () => {
		vi.useFakeTimers({ toFake: ['setTimeout'] });
		const { api } = setup(() => ({ status: 200, body: '{"tracks":[]}' }));

		await api.download('/projects/1/export.otio', 'teaser.otio');

		vi.advanceTimersByTime(59_999);
		expect(URL.revokeObjectURL).not.toHaveBeenCalled();
		vi.advanceTimersByTime(1);
		expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:export-1');
	});

	it('names the file after Content-Disposition, then after the path', async () => {
		const { api } = setup(({ url }) =>
			url.endsWith('/srt')
				? { body: '1', headers: { 'content-disposition': "attachment; filename*=UTF-8''sous-titres%20FR.srt" } }
				: { body: '1' }
		);

		await api.download('/projects/1/srt');
		await api.download('/projects/1/edl?fps=25');

		expect(clicked.map((link) => link.download)).toEqual(['sous-titres FR.srt', 'edl']);
	});

	it('throws on an error response without creating a file', async () => {
		const { api } = setup(() => json({ detail: 'Export not ready' }, 409));

		await expect(api.download('/projects/1/export.otio', 'cut.otio')).rejects.toMatchObject({
			status: 409,
			detail: 'Export not ready'
		});
		expect(URL.createObjectURL).not.toHaveBeenCalled();
		expect(clicked).toEqual([]);
	});
});
