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

	describe('admin routes', () => {
		it('lists every key with the status, owner and numeric page it is given', async () => {
			const get = vi.fn().mockResolvedValue({ data: { success: true, data: { items: [], total: 0, page: 2, page_size: 10 } } });
			const api = createApiKeysApi({ get } as unknown as AxiosInstance);

			await api.listAdminApiKeys({ status: 'active', userId: 'u1', query: '  raycast ', page: 2, pageSize: 10 });

			expect(get).toHaveBeenCalledWith('/api/admin/keys', { params: { status: 'active', page: 2, page_size: 10, user_id: 'u1', q: 'raycast' } });
		});

		it('defaults to all statuses, the first page and no owner filter', async () => {
			const get = vi.fn().mockResolvedValue({ data: { success: true } });
			const api = createApiKeysApi({ get } as unknown as AxiosInstance);

			await api.listAdminApiKeys();

			expect(get).toHaveBeenCalledWith('/api/admin/keys', { params: { status: 'all', page: 1, page_size: 50 } });
		});

		it('revokes another user\'s key through the admin route', async () => {
			const del = vi.fn().mockResolvedValue({ data: { success: true, data: { id: 'k1', revoked: true } } });
			const api = createApiKeysApi({ delete: del } as unknown as AxiosInstance);

			await api.revokeAdminApiKey('k1');

			expect(del).toHaveBeenCalledWith('/api/admin/keys/k1');
		});

		it('reads a key\'s history', async () => {
			const get = vi.fn().mockResolvedValue({ data: { success: true, data: [] } });
			const api = createApiKeysApi({ get } as unknown as AxiosInstance);

			await api.listAdminApiKeyEvents('k/1');

			expect(get).toHaveBeenCalledWith('/api/admin/keys/k%2F1/events');
		});

		it('reads and writes a user\'s API access', async () => {
			const get = vi.fn().mockResolvedValue({ data: { success: true, data: { keys_enabled: true, mcp_enabled: false } } });
			const put = vi.fn().mockResolvedValue({ data: { success: true, data: { keys_enabled: false, mcp_enabled: false } } });
			const api = createApiKeysApi({ get, put } as unknown as AxiosInstance);

			const read = await api.getUserApiAccess('u1');
			await api.setUserApiAccess('u1', { keys_enabled: false, mcp_enabled: false, keys: { active: 1, total: 2 } });

			expect(get).toHaveBeenCalledWith('/api/admin/users/u1/api-access');
			expect(read.data?.mcp_enabled).toBe(false);
			expect(put).toHaveBeenCalledWith('/api/admin/users/u1/api-access', { keys_enabled: false, mcp_enabled: false });
		});
	});
});
