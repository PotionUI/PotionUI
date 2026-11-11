import { describe, it, expect, vi } from 'vitest';
import type { AxiosInstance } from 'axios';
import { createSetupApi } from './setup';

describe('createSetupRun', () => {
	it('sends the recipe id and safe input without choosing a version', async () => {
		const post = vi.fn().mockResolvedValue({ data: { id: 'run-1' } });
		const api = createSetupApi({ post } as unknown as AxiosInstance);

		await api.createSetupRun('zimage-starter');

		expect(post).toHaveBeenCalledWith('/api/setup/runs', {
			recipe_id: 'zimage-starter',
			safe_input: {}
		});
		expect(Object.keys(post.mock.calls[0][1])).not.toContain('recipe_version');
	});
});
