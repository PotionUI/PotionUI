// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$lib/stores/auth', () => ({ authStore: { logout: vi.fn() } }));
vi.mock('$lib/services/api/index', () => ({ api: { getToken: () => null } }));

import { goto } from '$app/navigation';
import { authStore } from '$lib/stores/auth';
import { createGenerationSocket } from './websocket';

describe('createGenerationSocket', () => {
	it('logs out and redirects to login on an auth-failed close (4001), same as every other generation socket', () => {
		const ws = createGenerationSocket();

		// Simulate the server closing the connection with the auth-failure code,
		// which WebSocketService.onClose() turns into the onAuthFailed callback.
		(ws as unknown as { onClose(event: CloseEvent): void }).onClose({ code: 4001 } as CloseEvent);

		expect(authStore.logout).toHaveBeenCalledTimes(1);
		expect(goto).toHaveBeenCalledWith('/login?expired=1');
	});
});
