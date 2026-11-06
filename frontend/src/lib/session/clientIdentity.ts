import { randomUUID } from '$lib/utils/uuid';

export const SESSION_CLIENT_HEADER = 'X-Potion-Client';

export const WINDOW_CLIENT_ID = randomUUID();

export function sessionClientId(tabId: string, windowId: string = WINDOW_CLIENT_ID): string {
	return `${windowId}:${tabId}`;
}

export function isSameWindow(clientId: string | null | undefined, windowId: string = WINDOW_CLIENT_ID): boolean {
	return !!clientId && clientId.startsWith(`${windowId}:`);
}
