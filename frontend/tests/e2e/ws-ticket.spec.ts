import { test, expect } from '@playwright/test';
import { loginAsOwner } from './helpers';

const TICKET = /[?&]ticket=[A-Za-z0-9_-]{43}(&|$)/;

test('sockets connect through a single-use ticket, a reload reconnects with a new one, and no URL carries token=', async ({
	page
}) => {
	const socketUrls: string[] = [];
	const ticketRequests: string[] = [];
	page.on('websocket', (socket) => socketUrls.push(socket.url()));
	page.on('request', (request) => {
		if (request.method() === 'POST' && request.url().includes('/api/auth/ws-ticket')) {
			ticketRequests.push(request.postData() ?? '');
		}
	});

	await loginAsOwner(page);
	await page.goto('/generate');

	const generationSocket = () => socketUrls.filter((url) => url.includes('/ws/generation'));
	await expect.poll(() => generationSocket().length, { timeout: 20000 }).toBeGreaterThan(0);

	const firstTicket = generationSocket()[0].match(TICKET);
	expect(firstTicket, `generation socket URL ${generationSocket()[0]}`).not.toBeNull();
	expect(ticketRequests.some((body) => body.includes('/ws/generation'))).toBe(true);

	const before = socketUrls.length;
	await page.reload();
	await expect.poll(() => socketUrls.slice(before).filter((url) => url.includes('/ws/generation')).length, {
		timeout: 20000
	}).toBeGreaterThan(0);

	const reconnected = socketUrls.slice(before).find((url) => url.includes('/ws/generation'))!;
	expect(reconnected).toMatch(TICKET);
	expect(reconnected).not.toBe(generationSocket()[0]);

	expect(socketUrls.length).toBeGreaterThan(0);
	for (const url of socketUrls) {
		expect(url, 'every socket the page opens carries a ticket').toMatch(TICKET);
		expect(url, 'no socket URL carries a token').not.toContain('token=');
	}
});
