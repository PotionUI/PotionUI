import { describe, it, expect, vi } from 'vitest';
import type { AxiosInstance } from 'axios';
import { createApiKeysApi } from './apiKeys';

describe('createApiKeysApi', () => {
	it('lists keys with their limits', async () => {
		const get = vi.fn().mockResolvedValue({ data: { success: true, data: { keys: [], limits: { keys_enabled: true } } } });
		const api = createApiKeysApi({ get } as unknown as AxiosInstance);

		const result = await api.listApiKeys();

		expect(get).toHaveBeenCalledWith('/api/keys');
		expect(result.data?.limits.keys_enabled).toBe(true);
	});

	it('creates a key and returns the one-time secret', async () => {
		const post = vi.fn().mockResolvedValue({ data: { success: true, data: { key: { id: 'k1' }, secret: 'pui_abc' } } });
		const api = createApiKeysApi({ post } as unknown as AxiosInstance);
		const request = { name: 'Batch', scopes: ['read' as const, 'generate' as const], expires_in_days: null };

		const result = await api.createApiKey(request);

		expect(post).toHaveBeenCalledWith('/api/keys', request);
		expect(result.data?.secret).toBe('pui_abc');
	});

	it('PATCHes only the fields it is given', async () => {
		const patch = vi.fn().mockResolvedValue({ data: { success: true, data: { id: 'k1' } } });
		const api = createApiKeysApi({ patch } as unknown as AxiosInstance);

		await api.updateApiKey('k1', { name: 'Renamed' });

		expect(patch).toHaveBeenCalledWith('/api/keys/k1', { name: 'Renamed' });
	});

	it('revokes with DELETE', async () => {
		const del = vi.fn().mockResolvedValue({ data: { success: true, data: { id: 'k1', revoked: true } } });
		const api = createApiKeysApi({ delete: del } as unknown as AxiosInstance);

		const result = await api.revokeApiKey('k1');

		expect(del).toHaveBeenCalledWith('/api/keys/k1');
		expect(result.data?.revoked).toBe(true);
	});

	it('escapes an id before it goes into a path', async () => {
		const del = vi.fn().mockResolvedValue({ data: { success: true } });
		const api = createApiKeysApi({ delete: del } as unknown as AxiosInstance);

		await api.revokeApiKey('a/b');

		expect(del).toHaveBeenCalledWith('/api/keys/a%2Fb');
	});
});
