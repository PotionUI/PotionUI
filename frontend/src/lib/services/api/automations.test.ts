import type { AxiosInstance } from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createAutomationsApi } from './automations';

const client = {
	get: vi.fn(),
	post: vi.fn()
} as unknown as AxiosInstance;

const automations = createAutomationsApi(client);

describe('automation template API', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('lists the immutable template catalog', async () => {
		vi.mocked(client.get).mockResolvedValue({ data: { success: true, data: [] } });

		await automations.listAutomationTemplates();

		expect(client.get).toHaveBeenCalledWith('/api/automations/templates');
	});

	it('encodes namespaced keys and passes the optional copy name', async () => {
		vi.mocked(client.post).mockResolvedValue({ data: { success: true } });

		await automations.instantiateAutomationTemplate('plugin:example:starter', 'My copy');

		expect(client.post).toHaveBeenCalledWith(
			'/api/automations/templates/plugin%3Aexample%3Astarter/instantiate',
			{ name: 'My copy' }
		);
	});
});
