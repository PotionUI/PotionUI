import { api } from '$lib/services/api/index';
import { getWsUrl } from './wsUrl';

export async function fetchWsTicket(path: string): Promise<string> {
	const response = await api.getClient().post('/api/auth/ws-ticket', { path });
	const ticket = response.data?.data?.ticket;
	if (typeof ticket !== 'string' || ticket.length === 0) {
		throw new Error(`No WebSocket ticket was issued for ${path}`);
	}
	return ticket;
}

export async function ticketedWsUrl(path: string): Promise<string> {
	const ticket = await fetchWsTicket(path);
	return `${getWsUrl(path)}?ticket=${encodeURIComponent(ticket)}`;
}

export function isAuthRefusal(error: unknown): boolean {
	const status = (error as { response?: { status?: number } } | null)?.response?.status;
	return status === 401 || status === 403;
}

export function widgetWsUrl(path: string, token?: string | null): string | Promise<string> {
	return token ? getWsUrl(path, token) : ticketedWsUrl(path);
}
