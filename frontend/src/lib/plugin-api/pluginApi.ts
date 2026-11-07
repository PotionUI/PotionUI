import type { PluginFetchInit, PluginHostFetchApi } from './hostApi';
import { createPluginPageApi } from './pluginPageApi';

export interface PluginRequestInit extends RequestInit {
	timeout?: number;
	onUploadProgress?: (loaded: number, total: number) => void;
}

export interface PluginPageApi {
	getToken(): string | null;
	get(path: string): Promise<any>;
	post(path: string, data: unknown): Promise<any>;
	put(path: string, data: unknown): Promise<any>;
	delete(path: string): Promise<any>;
	uploadFile(path: string, formData: FormData): Promise<any>;
	fetchFromApp(path: string): Promise<any>;
	request(method: string, path: string, data?: unknown): Promise<any>;
	patch(path: string, data: unknown): Promise<any>;
	fetchRaw(path: string, init?: PluginRequestInit): Promise<Response>;
	fetchRawFromApp(path: string, init?: PluginRequestInit): Promise<Response>;
	download(path: string, filename?: string): Promise<void>;
}

export class PluginApiError extends Error {
	readonly status: number;
	readonly body: unknown;
	readonly detail: unknown;

	constructor(status: number, body: unknown) {
		const detail = isRecord(body) && 'detail' in body ? body.detail : undefined;
		super(errorMessage(status, detail));
		this.name = 'PluginApiError';
		this.status = status;
		this.body = body;
		this.detail = detail;
	}
}

const DOWNLOAD_URL_LIFETIME_MS = 60_000;

function isRecord(value: unknown): value is Record<string, unknown> {
	return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function errorMessage(status: number, detail: unknown): string {
	if (typeof detail === 'string' && detail) return detail;
	if (isRecord(detail) && typeof detail.message === 'string' && detail.message) return detail.message;
	return `Request failed with status ${status}`;
}

async function readBody(res: Response): Promise<unknown> {
	if (res.status === 204) return null;
	const text = await res.text();
	if (!text) return null;
	try {
		return JSON.parse(text);
	} catch {
		return text;
	}
}

function filenameFrom(res: Response, path: string): string {
	const disposition = res.headers.get('Content-Disposition') ?? '';
	const encoded = disposition.match(/filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/);
	if (encoded) {
		try {
			return decodeURIComponent(encoded[1].trim());
		} catch {
			return encoded[1].trim();
		}
	}
	const plain = disposition.match(/filename\s*=\s*"?([^";]+)"?/);
	if (plain) return plain[1].trim();
	return path.split('?')[0].split('/').filter(Boolean).pop() || 'download';
}

function toFetchInit(init: PluginRequestInit): PluginFetchInit {
	const headers = Object.fromEntries(new Headers(init.headers).entries());
	const body = init.body ?? undefined;
	if (body instanceof Blob && !('content-type' in headers)) {
		headers['content-type'] = body.type || 'application/octet-stream';
	}
	return {
		method: init.method,
		headers,
		body,
		signal: init.signal ?? undefined,
		timeout: init.timeout ?? 0,
		onUploadProgress: init.onUploadProgress
	};
}

export function createPluginApi(
	pluginId: string,
	host: PluginHostFetchApi,
	getToken: () => string | null
): PluginPageApi {
	const page = createPluginPageApi(host, getToken, pluginId);
	const fetchRawFromApp = (path: string, init: PluginRequestInit = {}) => host.fetch(path, toFetchInit(init));
	const fetchRaw = (path: string, init?: PluginRequestInit) =>
		fetchRawFromApp(`/api/plugins/${pluginId}${path}`, init);

	const request = async (method: string, path: string, data?: unknown) => {
		const init: PluginRequestInit = { method: method.toUpperCase() };
		if (data !== undefined) {
			init.headers = { 'Content-Type': 'application/json' };
			init.body = JSON.stringify(data);
		}
		const res = await fetchRaw(path, init);
		const body = await readBody(res);
		if (!res.ok) throw new PluginApiError(res.status, body);
		return body;
	};

	return {
		...page,
		request,
		patch: (path, data) => request('PATCH', path, data),
		fetchRaw,
		fetchRawFromApp,
		download: async (path, filename) => {
			const res = await fetchRaw(path);
			if (!res.ok) throw new PluginApiError(res.status, await readBody(res));
			const url = URL.createObjectURL(await res.blob());
			const link = document.createElement('a');
			link.href = url;
			link.download = filename || filenameFrom(res, path);
			link.style.display = 'none';
			document.body.appendChild(link);
			try {
				link.click();
			} finally {
				link.remove();
				setTimeout(() => URL.revokeObjectURL(url), DOWNLOAD_URL_LIFETIME_MS);
			}
		}
	};
}
