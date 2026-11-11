import { describe, it, expect, vi } from 'vitest';
import type { AxiosInstance } from 'axios';
import { createMcpToolsApi } from './mcpTools';

describe('createMcpToolsApi', () => {
	it('reads the user default level', async () => {
		const get = vi.fn().mockResolvedValue({ data: { success: true, data: { level: 'default', tools: [] } } });
		const api = createMcpToolsApi({ get } as unknown as AxiosInstance);

		const result = await api.getMcpTools();

		expect(get).toHaveBeenCalledWith('/api/mcp/tools');
		expect(result.data?.level).toBe('default');
	});

	it('PATCHes one override per toggle', async () => {
		const patch = vi.fn().mockResolvedValue({ data: { success: true, data: { level: 'default', tools: [] } } });
		const api = createMcpToolsApi({ patch } as unknown as AxiosInstance);

		await api.patchMcpTools({ overrides: { start_generation: false } });

		expect(patch).toHaveBeenCalledWith('/api/mcp/tools', { overrides: { start_generation: false } });
	});

	it('PATCHes the policy on its own', async () => {
		const patch = vi.fn().mockResolvedValue({ data: { success: true } });
		const api = createMcpToolsApi({ patch } as unknown as AxiosInstance);

		await api.patchMcpTools({ policy: 'all_on' });

		expect(patch).toHaveBeenCalledWith('/api/mcp/tools', { policy: 'all_on' });
	});

	it('reads and narrows one key', async () => {
		const get = vi.fn().mockResolvedValue({ data: { success: true, data: { level: 'key', key_id: 'k1' } } });
		const patch = vi.fn().mockResolvedValue({ data: { success: true } });
		const api = createMcpToolsApi({ get, patch } as unknown as AxiosInstance);

		await api.getMcpKeyTools('k1');
		await api.patchMcpKeyTools('k1', { overrides: { write_memory: false, list_models: null } });
		await api.patchMcpKeyTools('k1', { reset: true });

		expect(get).toHaveBeenCalledWith('/api/mcp/keys/k1/tools');
		expect(patch).toHaveBeenNthCalledWith(1, '/api/mcp/keys/k1/tools', { overrides: { write_memory: false, list_models: null } });
		expect(patch).toHaveBeenNthCalledWith(2, '/api/mcp/keys/k1/tools', { reset: true });
	});
});
