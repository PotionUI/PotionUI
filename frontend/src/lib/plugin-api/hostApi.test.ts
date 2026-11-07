import { describe, it, expect, vi, beforeEach } from 'vitest';
import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios';
import { APIClient } from '$lib/services/api/client';
import { createHostFetchApi } from './hostApi';

vi.mock('$lib/utils/storage', () => ({
	storage: { get: () => 'tok-1', set: () => undefined, remove: () => undefined }
}));

interface Reply {
	status: number;
	data?: unknown;
	headers?: Record<string, string>;
	error?: Error;
}

function harness(reply: Reply) {
	const seen: InternalAxiosRequestConfig[] = [];
	const client = new APIClient('');
	client.getClient().defaults.adapter = async (config) => {
		seen.push(config);
		if (reply.error) throw reply.error;
		return {
			data: reply.data ?? new Blob([]),
			status: reply.status,
			statusText: '',
			headers: reply.headers ?? {},
			config
		};
	};
	const expired = vi.fn();
	client.setOnAuthExpired(expired);
	const host = createHostFetchApi(client);
	return { client, host, seen, expired };
}

const DOUBLE_SLASH = '/'.repeat(2);
const jsonBlob = (value: unknown) => new Blob([JSON.stringify(value)], { type: 'application/json' });

describe('host fetch api', () => {
	let h: ReturnType<typeof harness>;

	beforeEach(() => {
		h = harness({ status: 200, data: jsonBlob({ ok: true }) });
	});

	it('sends the bearer token and resolves the parsed body', async () => {
		expect(await h.host.json('/api/things')).toEqual({ ok: true });
		expect(h.seen[0].headers.Authorization).toBe('Bearer tok-1');
		expect(h.seen[0].url).toBe('/api/things');
		expect(h.seen[0].withCredentials).toBe(true);
	});

	it.each([400, 404, 500])('resolves a %i with its body instead of throwing', async (status) => {
		const e = harness({ status, data: jsonBlob({ detail: 'nope' }) });
		expect(await e.host.json('/api/things')).toEqual({ detail: 'nope' });
		const res = await e.host.fetch('/api/things');
		expect(res.ok).toBe(false);
		expect(res.status).toBe(status);
		expect(e.seen[0].validateStatus?.(status)).toBe(true);
		expect(e.expired).not.toHaveBeenCalled();
	});

	it('triggers session expiry exactly once on a 401, with the token that was sent', async () => {
		const e = harness({ status: 401, data: jsonBlob({ detail: 'expired' }) });
		expect(await e.host.json('/api/things')).toEqual({ detail: 'expired' });
		expect(e.expired).toHaveBeenCalledTimes(1);
		expect(e.expired).toHaveBeenCalledWith('tok-1');
	});

	it.each(['/api/auth/login', '/api/auth/register'])('does not treat a 401 from %s as an expired session', async (url) => {
		const e = harness({ status: 401, data: jsonBlob({}) });
		await e.host.json(url, { method: 'POST' });
		expect(e.expired).not.toHaveBeenCalled();
	});

	it('passes caller headers through to the client', async () => {
		await h.host.json('/api/things', { headers: { 'X-Trace': 'abc' } });
		expect(h.seen[0].headers['X-Trace']).toBe('abc');
	});

	it.each([`https:${DOUBLE_SLASH}evil.example/api/things`, `${DOUBLE_SLASH}evil.example/api/things`, 'api/things', '/\\evil.example', ''])(
		'refuses %j so the token never leaves the app',
		async (path) => {
			await expect(h.host.fetch(path)).rejects.toBeInstanceOf(TypeError);
			expect(h.seen).toHaveLength(0);
		}
	);

	it('adopts a refreshed token from the response', async () => {
		const e = harness({ status: 200, data: jsonBlob({}), headers: { 'x-refreshed-token': 'tok-2' } });
		await e.host.json('/api/things');
		expect(e.client.getToken()).toBe('tok-2');
	});

	it('keeps the default timeout for JSON calls and lifts it for multipart', async () => {
		await h.host.json('/api/things', { method: 'POST', body: '{}' });
		expect(h.seen[0].timeout).toBe(30000);
		const form = new FormData();
		form.append('file', new Blob(['x']), 'a.bin');
		await h.host.json('/api/upload', { method: 'POST', body: form });
		expect(h.seen[1].timeout).toBe(0);
		expect(h.seen[1].data).toBeInstanceOf(FormData);
		await h.host.fetch('/api/slow', { timeout: 0 });
		expect(h.seen[2].timeout).toBe(0);
	});

	it('reports the upload progress of a multipart body', async () => {
		const progress: [number, number][] = [];
		const form = new FormData();
		h.client.getClient().defaults.adapter = async (config) => {
			config.onUploadProgress?.({ loaded: 5, total: 20, bytes: 5, lengthComputable: true });
			config.onUploadProgress?.({ loaded: 20, total: undefined, bytes: 15, lengthComputable: false });
			return { data: jsonBlob({ ok: true }), status: 200, statusText: '', headers: {}, config };
		};
		await h.host.json('/api/upload', { method: 'POST', body: form, onUploadProgress: (loaded, total) => progress.push([loaded, total]) });
		expect(progress).toEqual([
			[5, 20],
			[20, 0]
		]);
	});

	it('returns a Response whose blob() carries the bytes', async () => {
		const e = harness({ status: 200, data: new Blob(['zip-bytes'], { type: 'application/zip' }) });
		const res = await e.host.fetch('/api/export', { method: 'POST', body: '{}' });
		expect(res.ok).toBe(true);
		expect(await (await res.blob()).text()).toBe('zip-bytes');
	});

	it('resolves null for a body that is not JSON', async () => {
		const e = harness({ status: 502, data: new Blob(['<html>bad gateway</html>']) });
		expect(await e.host.json('/api/things')).toBeNull();
	});

	it('builds a Response for a 204 without a body', async () => {
		const e = harness({ status: 204 });
		const res = await e.host.fetch('/api/things', { method: 'DELETE' });
		expect(res.status).toBe(204);
		expect(await res.text()).toBe('');
	});

	it('rejects when the network fails', async () => {
		const e = harness({ status: 0, error: new AxiosError('Network Error', 'ERR_NETWORK') });
		await expect(e.host.json('/api/things')).rejects.toBeInstanceOf(TypeError);
	});

	it('rejects with an AbortError when the caller aborts', async () => {
		const e = harness({ status: 0, error: new axios.CanceledError('canceled') });
		await expect(e.host.fetch('/api/things')).rejects.toMatchObject({ name: 'AbortError' });
	});
});
