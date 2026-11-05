import type { AxiosInstance } from 'axios';
import { api } from '$lib/services/api/index';
import type { RunPage, RunQuery } from '$lib/elixirs/types';

export interface ElixirApiError {
	code: string;
	message: string;
	issues?: unknown[];
	[key: string]: unknown;
}

export type ElixirResult<T = any> = { ok: true; data: T } | { ok: false; error: ElixirApiError };

type Body = Record<string, unknown>;

const BASE = '/api/elixirs';

function failure(body: any, fallback: string): ElixirApiError {
	const detail = body?.detail;
	if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
		return { code: detail.code ?? 'error', message: detail.message ?? fallback, ...detail };
	}
	if (Array.isArray(detail)) {
		const first = detail[0];
		return { code: 'invalid', message: first?.msg ? `The change was refused: ${first.msg}` : fallback };
	}
	return { code: 'error', message: fallback };
}

async function call<T = any>(run: () => Promise<any>, fallback: string): Promise<ElixirResult<T>> {
	try {
		const body = await run();
		if (body && body.detail !== undefined) return { ok: false, error: failure(body, fallback) };
		return { ok: true, data: body as T };
	} catch {
		return { ok: false, error: { code: 'network', message: fallback } };
	}
}

export function runQueryString(query: RunQuery): string {
	const params = new URLSearchParams();
	for (const [key, value] of Object.entries(query)) {
		if (value === undefined || value === null || value === '') continue;
		params.set(key, String(value));
	}
	const text = params.toString();
	return text ? `?${text}` : '';
}

function id(value: string): string {
	return encodeURIComponent(value);
}

export function createElixirsApi(client: AxiosInstance) {
	const request = async (method: string, url: string, data?: unknown): Promise<any> => {
		const response = await client.request({ url, method, data, validateStatus: () => true });
		return response.data ?? null;
	};
	const get = (url: string) => request('GET', url);
	const post = (url: string, data: unknown = {}) => request('POST', url, data);
	const put = (url: string, data: unknown) => request('PUT', url, data);
	const del = (url: string) => request('DELETE', url);
	const elixir = (elixirId: string) => `${BASE}/${id(elixirId)}`;
	const node = (elixirId: string, nodeId: string) => `${elixir(elixirId)}/nodes/${id(nodeId)}`;
	const run = (runId: string) => `${BASE}/runs/${id(runId)}`;

	return {
		listElixirs: () => call(() => get(BASE), 'Elixirs could not be loaded.'),
		getElixir: (elixirId: string) => call(() => get(elixir(elixirId)), 'This elixir could not be loaded.'),
		createElixir: (name: string) => call(() => post(BASE, { name, run_mode: 'review' }), 'The elixir could not be created.'),
		updateElixir: (elixirId: string, body: Body) => call(() => put(elixir(elixirId), body), 'The elixir could not be saved.'),
		deleteElixir: (elixirId: string) => call(() => del(elixir(elixirId)), 'The elixir could not be deleted.'),
		validateElixir: (elixirId: string) => call(() => post(`${elixir(elixirId)}/validate`), 'The elixir could not be checked.'),
		importSession: (elixirId: string, nodeId: string, body: Body) =>
			call(() => post(`${node(elixirId, nodeId)}/session`, body), 'The session could not be copied.'),
		followSession: (elixirId: string, nodeId: string, body: Body) =>
			call(() => post(`${node(elixirId, nodeId)}/session/follow`, body), 'The session could not be followed.'),
		copySession: (elixirId: string, nodeId: string, body: Body) =>
			call(() => post(`${node(elixirId, nodeId)}/session/copy`, body), 'The copy could not be made.'),
		ownSettings: (elixirId: string, nodeId: string, body: Body) =>
			call(() => post(`${node(elixirId, nodeId)}/session/new`, body), 'The step could not switch to its own settings.'),
		followedSessions: (elixirId: string) => call(() => get(`${elixir(elixirId)}/followed-sessions`), 'Followed sessions could not be read.'),
		saveSession: async (body: Body): Promise<ElixirResult> => {
			const result = await call(() => post('/api/sessions/save', body), 'The session could not be saved.');
			if (!result.ok) return result;
			return result.data?.success === false || !result.data?.data?.id
				? { ok: false, error: { code: 'error', message: result.data?.message || 'The session could not be saved.' } }
				: { ok: true, data: result.data.data };
		},
		listPresets: () => call(() => get(`${BASE}/presets`), 'Presets could not be loaded.'),
		startBrew: (elixirId: string, body: Body) => call(() => post(`${elixir(elixirId)}/runs`, body), 'The brew could not be started.'),
		runNode: (elixirId: string, nodeId: string, body: Body) =>
			call(() => post(`${node(elixirId, nodeId)}/runs`, body), 'The node could not be run.'),
		listRuns: (elixirId: string, query: RunQuery = {}) =>
			call<RunPage>(() => get(`${elixir(elixirId)}/runs${runQueryString(query)}`), 'Earlier brews could not be loaded.'),
		cancelRuns: (elixirId: string, body: { run_ids: string[] } | { all_active: true }) =>
			call<{ cancelled: string[] }>(() => post(`${elixir(elixirId)}/runs/cancel`, body), 'The brews could not be stopped.'),
		getRun: (runId: string) => call(() => get(run(runId)), 'The brew could not be loaded.'),
		saveSelection: (runId: string, edgeId: string, body: Body) =>
			call(() => put(`${run(runId)}/edges/${id(edgeId)}/selection`, body), 'The choice could not be saved.'),
		continueEdge: (runId: string, edgeId: string, body: Body) =>
			call(() => post(`${run(runId)}/edges/${id(edgeId)}/continue`, body), 'The brew could not continue.'),
		editText: (runId: string, textId: string, body: Body) =>
			call(() => put(`${run(runId)}/texts/${id(textId)}`, body), 'The prompt could not be changed.'),
		cancelRun: (runId: string) => call(() => post(`${run(runId)}/cancel`), 'The brew could not be stopped.'),
		retryFailed: (runId: string, nodeId: string) =>
			call(() => post(`${run(runId)}/nodes/${id(nodeId)}/retry-failed`), 'The failed work could not be retried.'),
		listSessions: async (presetId: string): Promise<ElixirResult<any[]>> => {
			const result = await call(() => get(`/api/sessions/preset/${id(presetId)}`), 'Saved sessions could not be loaded.');
			if (!result.ok) return result;
			return result.data?.success === false
				? { ok: false, error: { code: 'error', message: 'Saved sessions could not be loaded.' } }
				: { ok: true, data: Array.isArray(result.data?.data) ? result.data.data : [] };
		},
		presetVars: async (presetId: string): Promise<ElixirResult<Record<string, unknown>>> => {
			const result = await call(() => get(`/api/presets/${id(presetId)}`), 'The preset could not be loaded.');
			if (!result.ok) return result;
			return result.data?.success === false
				? { ok: false, error: { code: 'error', message: 'The preset could not be loaded.' } }
				: { ok: true, data: result.data?.data?.vars ?? {} };
		},
		listVersions: async (sessionId: string): Promise<ElixirResult<any[]>> => {
			const result = await call(() => get(`/api/sessions/${id(sessionId)}/versions?limit=50`), 'Session versions could not be loaded.');
			if (!result.ok) return result;
			return { ok: true, data: Array.isArray(result.data?.data) ? result.data.data : [] };
		}
	};
}

export type ElixirsApi = ReturnType<typeof createElixirsApi>;

let shared: ElixirsApi | null = null;

export function elixirsApi(): ElixirsApi {
	shared ??= createElixirsApi(api.getClient());
	return shared;
}
