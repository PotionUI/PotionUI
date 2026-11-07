import axios, { type AxiosInstance } from 'axios';

export interface HostFetchClient {
	getClient(): AxiosInstance;
	triggerAuthExpired(requestToken: string | null): void;
}

export interface PluginFetchInit {
	method?: string;
	headers?: Record<string, string>;
	body?: unknown;
	signal?: AbortSignal;
	timeout?: number;
	onUploadProgress?: (loaded: number, total: number) => void;
}

export interface PluginHostFetchApi {
	fetch: (path: string, init?: PluginFetchInit) => Promise<Response>;
	json: (path: string, init?: PluginFetchInit) => Promise<any>;
}

const NULL_BODY_STATUSES = new Set([101, 204, 205, 304]);
const SAME_ORIGIN_PATH = /^\/(?![/\\])/;
const AUTH_EXEMPT = ['/api/auth/login', '/api/auth/register'];

function bearerTokenOf(header: unknown): string | null {
	if (typeof header !== 'string') return null;
	return header.startsWith('Bearer ') ? header.slice(7) : null;
}

function toBody(data: unknown, status: number): BodyInit | null {
	if (NULL_BODY_STATUSES.has(status) || data == null) return null;
	if (data instanceof Blob || typeof data === 'string' || data instanceof ArrayBuffer) return data;
	return JSON.stringify(data);
}

function toHeaders(raw: unknown): Headers {
	const headers = new Headers();
	const entries = (raw as { toJSON?: () => Record<string, unknown> })?.toJSON?.() ?? raw ?? {};
	for (const [key, value] of Object.entries(entries as Record<string, unknown>)) {
		if (typeof value === 'string') headers.set(key, value);
	}
	return headers;
}

export function createHostFetchApi(client: HostFetchClient): PluginHostFetchApi {
	async function pluginFetch(path: string, init: PluginFetchInit = {}): Promise<Response> {
		if (!SAME_ORIGIN_PATH.test(path)) throw new TypeError('Plugin requests must use a path on this app');
		const isForm = typeof FormData !== 'undefined' && init.body instanceof FormData;
		const onUploadProgress = init.onUploadProgress;
		const response = await client
			.getClient()
			.request({
				url: path,
				method: init.method ?? 'GET',
				headers: init.headers,
				data: init.body,
				signal: init.signal,
				timeout: init.timeout ?? (isForm ? 0 : undefined),
				withCredentials: true,
				onUploadProgress: onUploadProgress && ((event) => onUploadProgress(event.loaded, event.total ?? 0)),
				responseType: 'blob',
				validateStatus: () => true
			})
			.catch((err) => {
				if (axios.isCancel(err)) throw new DOMException('The operation was aborted', 'AbortError');
				throw new TypeError(err instanceof Error ? err.message : 'Network request failed');
			});
		if (response.status === 401 && !AUTH_EXEMPT.some((url) => path.includes(url))) {
			client.triggerAuthExpired(bearerTokenOf(response.config?.headers?.Authorization));
		}
		return new Response(toBody(response.data, response.status), {
			status: response.status,
			statusText: response.statusText,
			headers: toHeaders(response.headers)
		});
	}

	async function pluginJson(path: string, init?: PluginFetchInit): Promise<any> {
		const response = await pluginFetch(path, init);
		return response.json().catch(() => null);
	}

	return { fetch: pluginFetch, json: pluginJson };
}
