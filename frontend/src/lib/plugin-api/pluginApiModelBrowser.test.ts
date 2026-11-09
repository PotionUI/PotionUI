import { describe, it, expect, vi } from 'vitest';
import axios from 'axios';
import { resolve } from 'node:path';
import { createHostFetchApi } from './hostApi';
import { createPluginApi } from './pluginApi';

const MODEL_BROWSER_CLIENT = resolve(
	__dirname,
	'../../../../content/plugins/marketplace/model-browser/frontend/src/lib/client.js'
);

function json(body: unknown, status = 200) {
	return { status, body: JSON.stringify(body) };
}

function route(url: string): { status: number; body: string } {
	if (url.endsWith('/me')) return json({ success: true, data: { id: 'u1' } });
	if (url.includes('/search')) {
		return json({ detail: { success: false, error: 'rate_limited', message: 'try again in 30 s' } }, 429);
	}
	if (url.includes('/models/')) return json({ detail: { success: false, error: 'not_found', message: 'Gone' } }, 404);
	return { status: 500, body: '<html>oops</html>' };
}

describe('model-browser client on the host-backed plugin api', () => {
	it('reads success, error envelopes, rate limits and broken bodies as before', async () => {
		const { getMe, getModel, searchModels, callPlugin } = await import(/* @vite-ignore */ MODEL_BROWSER_CLIENT);
		const client = axios.create();
		client.defaults.adapter = async (config) => {
			const reply = route(config.url ?? '');
			return { data: new Blob([reply.body]), status: reply.status, statusText: '', headers: {}, config };
		};
		const host = createHostFetchApi({ getClient: () => client, triggerAuthExpired: vi.fn() });
		const api = createPluginApi('model-browser', host, () => 'tok');

		expect(await getMe(api)).toEqual({ ok: true, data: { id: 'u1' } });
		expect(await getModel(api, 'civitai', '1')).toEqual({
			ok: false,
			error: { status: 404, code: 'not_found', message: 'Gone', retryAfter: null }
		});
		expect((await searchModels(api, 'civitai', '')).error).toEqual({
			status: 429,
			code: 'rate_limited',
			message: 'try again in 30 s',
			retryAfter: 30
		});
		expect((await callPlugin(api, 'get', '/broken')).error.code).toBe('invalid_response');
	});
});
