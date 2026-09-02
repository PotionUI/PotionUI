import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { InternalAxiosRequestConfig } from 'axios';
import { APIClient } from './client';

vi.mock('$lib/utils/storage', () => ({
	storage: { get: () => null, set: () => undefined, remove: () => undefined }
}));

function clientCapturing(seen: InternalAxiosRequestConfig[]) {
	const api = new APIClient('');
	api.getClient().defaults.adapter = async (config) => {
		seen.push(config);
		return { data: { success: true }, status: 200, statusText: 'OK', headers: {}, config };
	};
	return api;
}

describe('APIClient request bodies', () => {
	let seen: InternalAxiosRequestConfig[];
	beforeEach(() => {
		seen = [];
	});

	it('sends a FormData body as multipart, never re-serialised to JSON', async () => {
		const api = clientCapturing(seen);
		const formData = new FormData();
		formData.append('file', new Blob(['png-bytes'], { type: 'image/png' }), 'pasted.png');

		await api.getClient().post('/api/media/upload', formData);

		expect(seen).toHaveLength(1);
		expect(seen[0].data).toBeInstanceOf(FormData);
		expect(String(seen[0].headers['Content-Type'])).toContain('multipart/form-data');
	});

	it('keeps JSON for plain object bodies', async () => {
		const api = clientCapturing(seen);

		await api.getClient().post('/api/things', { a: 1 });

		expect(seen[0].data).toBe(JSON.stringify({ a: 1 }));
		expect(String(seen[0].headers['Content-Type'])).toContain('application/json');
	});
});
