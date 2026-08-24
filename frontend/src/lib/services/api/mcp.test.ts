import { describe, it, expect, vi } from 'vitest';
import type { AxiosInstance } from 'axios';
import { createMcpApi } from './mcp';

describe('getMcpStatus', () => {
	it('GETs the status endpoint', async () => {
		const get = vi
			.fn()
			.mockResolvedValue({ data: { success: true, data: { enabled: false, global_enabled: true, user_enabled: false } } });
		const client = { get } as unknown as AxiosInstance;
		const api = createMcpApi(client);

		const result = await api.getMcpStatus();

		expect(get).toHaveBeenCalledWith('/api/mcp/status');
		expect(result.data?.enabled).toBe(false);
	});
});

describe('listMcpTokens', () => {
	it('GETs the tokens list endpoint', async () => {
		const get = vi.fn().mockResolvedValue({ data: { success: true, data: [] } });
		const client = { get } as unknown as AxiosInstance;
		const api = createMcpApi(client);

		const result = await api.listMcpTokens();

		expect(get).toHaveBeenCalledWith('/api/mcp/tokens');
		expect(result.success).toBe(true);
	});
});

describe('createMcpToken', () => {
	it('POSTs the name and returns the one-time full token', async () => {
		const post = vi.fn().mockResolvedValue({
			data: {
				success: true,
				data: {
					id: 't1',
					name: 'Claude Desktop',
					token: 'secret-value',
					token_prefix: 'pk_ab12',
					created_at: '2026-08-18T00:00:00Z'
				}
			}
		});
		const client = { post } as unknown as AxiosInstance;
		const api = createMcpApi(client);

		const result = await api.createMcpToken('Claude Desktop');

		expect(post).toHaveBeenCalledWith('/api/mcp/tokens', { name: 'Claude Desktop' });
		expect(result.data?.token).toBe('secret-value');
	});
});

describe('revokeMcpToken', () => {
	it('DELETEs the token by id', async () => {
		const del = vi.fn().mockResolvedValue({ data: { success: true, data: { id: 't1', revoked: true } } });
		const client = { delete: del } as unknown as AxiosInstance;
		const api = createMcpApi(client);

		const result = await api.revokeMcpToken('t1');

		expect(del).toHaveBeenCalledWith('/api/mcp/tokens/t1');
		expect(result.data?.revoked).toBe(true);
	});
});
