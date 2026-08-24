import { describe, it, expect, vi, afterEach } from 'vitest';
import { api } from './index';

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('chat SSE auth expiry wiring', () => {
	it('invokes the onAuthExpired callback registered via setOnAuthExpired when a chat stream 401s', async () => {
		const fetchMock = vi.fn().mockResolvedValue({ status: 401, ok: false, statusText: 'Unauthorized' });
		vi.stubGlobal('fetch', fetchMock);

		const onAuthExpired = vi.fn();
		api.setOnAuthExpired(onAuthExpired);

		await expect(api.sendChatMessageStream('session-1', { content: 'hi' })).rejects.toThrow(
			'Authentication expired'
		);

		expect(onAuthExpired).toHaveBeenCalledTimes(1);
	});
});
