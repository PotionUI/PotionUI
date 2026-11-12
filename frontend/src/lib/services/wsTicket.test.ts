// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';

const post = vi.hoisted(() => vi.fn());
vi.mock('$lib/services/api/index', () => ({ api: { getClient: () => ({ post }) } }));

import { widgetWsUrl } from './wsTicket';
import { getWsUrl } from './wsUrl';

describe('widgetWsUrl', () => {
	it('keeps the v0.1.0 call shape: a token argument returns the URL synchronously with ?token=', () => {
		const url = widgetWsUrl('/ws/system', 'tok en');
		expect(typeof url).toBe('string');
		expect(url).toBe(getWsUrl('/ws/system', 'tok en'));
		expect(url).toContain('/ws/system?token=tok%20en');
		expect(post).not.toHaveBeenCalled();
	});

	it('without a token it resolves to a ticketed URL', async () => {
		post.mockResolvedValue({ data: { data: { ticket: 'abc' } } });
		const url = await widgetWsUrl('/ws/system');
		expect(url).toBe(`${getWsUrl('/ws/system')}?ticket=abc`);
		expect(post).toHaveBeenCalledWith('/api/auth/ws-ticket', { path: '/ws/system' });
	});
});

describe('getWsUrl', () => {
	it('adds no query without a token', () => {
		expect(getWsUrl('/ws/generation')).not.toContain('?');
	});
});
