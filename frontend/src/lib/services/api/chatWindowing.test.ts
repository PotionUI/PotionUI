import { describe, it, expect, vi } from 'vitest';
import type { AxiosInstance } from 'axios';
import { createChatApi } from './chat';

/**
 * `getChatSession`'s `tail` option and `getChatMessagesBefore` are the
 * client half of the windowed-transcript contract: only the URL/query
 * construction is this module's responsibility (the backend half is a
 * separate lane) — these assert the request shape, not any parsing beyond
 * `response.data` passthrough already covered elsewhere in this file.
 */

function fakeClient(data: unknown): AxiosInstance {
	return { get: vi.fn().mockResolvedValue({ data }) } as unknown as AxiosInstance;
}

describe('getChatSession tail option', () => {
	it('omits the query string when no tail is given (unchanged full-load behavior)', async () => {
		const client = fakeClient({ success: true, data: { id: 's1', messages: [] } });
		const api = createChatApi(client, () => null, () => 'http://test');

		await api.getChatSession('s1');

		expect(client.get).toHaveBeenCalledWith('/api/chat/sessions/s1');
	});

	it('appends ?tail=N when a tail is requested', async () => {
		const client = fakeClient({ success: true, data: { id: 's1', messages: [] } });
		const api = createChatApi(client, () => null, () => 'http://test');

		await api.getChatSession('s1', { tail: 60 });

		expect(client.get).toHaveBeenCalledWith('/api/chat/sessions/s1?tail=60');
	});

	it('returns the response body unchanged (message_count/has_earlier pass through)', async () => {
		const payload = {
			success: true,
			data: { id: 's1', messages: [{ id: 'm1' }], message_count: 200, has_earlier: true }
		};
		const client = fakeClient(payload);
		const api = createChatApi(client, () => null, () => 'http://test');

		const result = await api.getChatSession('s1', { tail: 60 });

		expect(result).toEqual(payload);
	});
});

describe('getChatMessagesBefore', () => {
	it('requests the before cursor with the given (or default) limit', async () => {
		const client = fakeClient({ success: true, data: { messages: [], has_earlier: false } });
		const api = createChatApi(client, () => null, () => 'http://test');

		await api.getChatMessagesBefore('s1', 'm100');

		expect(client.get).toHaveBeenCalledWith('/api/chat/sessions/s1/messages?before=m100&limit=60');
	});

	it('honors an explicit limit', async () => {
		const client = fakeClient({ success: true, data: { messages: [], has_earlier: false } });
		const api = createChatApi(client, () => null, () => 'http://test');

		await api.getChatMessagesBefore('s1', 'm100', 20);

		expect(client.get).toHaveBeenCalledWith('/api/chat/sessions/s1/messages?before=m100&limit=20');
	});

	it('returns the response body unchanged', async () => {
		const payload = { success: true, data: { messages: [{ id: 'm1' }, { id: 'm2' }], has_earlier: true } };
		const client = fakeClient(payload);
		const api = createChatApi(client, () => null, () => 'http://test');

		const result = await api.getChatMessagesBefore('s1', 'm100');

		expect(result).toEqual(payload);
	});
});
