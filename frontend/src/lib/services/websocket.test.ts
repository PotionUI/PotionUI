// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';

vi.mock('$lib/stores/auth', () => ({ authStore: { expireSession: vi.fn() } }));
vi.mock('$lib/services/api/index', () => ({ api: { getToken: () => null } }));

import { authStore } from '$lib/stores/auth';
import { createGenerationSocket } from './websocket';

describe('createGenerationSocket', () => {
	it('expires the session on an auth-failed close (4001), same as every other generation socket', () => {
		const ws = createGenerationSocket();

		// Simulate the server closing the connection with the auth-failure code,
		// which WebSocketService.onClose() turns into the onAuthFailed callback.
		(ws as unknown as { onClose(event: CloseEvent): void }).onClose({ code: 4001 } as CloseEvent);

		expect(authStore.expireSession).toHaveBeenCalledTimes(1);
	});
});
