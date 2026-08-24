import type { AxiosInstance } from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createGenerationsApi } from './generations';

const client = {
	get: vi.fn(),
	post: vi.fn()
} as unknown as AxiosInstance;

const generations = createGenerationsApi(client);

describe('getGenerationProfileReport', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('requests the report format as text and returns the raw string', async () => {
		vi.mocked(client.get).mockResolvedValue({ data: 'STAGE TABLE\n...report...' });

		const report = await generations.getGenerationProfileReport('gen-123');

		expect(client.get).toHaveBeenCalledWith('/api/generations/gen-123/profile', {
			params: { format: 'report' },
			responseType: 'text'
		});
		expect(report).toBe('STAGE TABLE\n...report...');
	});
});
