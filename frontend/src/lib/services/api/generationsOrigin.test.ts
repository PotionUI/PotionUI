import { describe, it, expect, vi } from 'vitest';
import { createGenerationsApi } from './generations';

function clientReturning() {
	const get = vi.fn(async () => ({ data: { success: true, data: { generations: [], total: 0 } } }));
	return { client: { get } as never, get };
}

describe('getGenerationHistory origin filter', () => {
	it('puts the chosen source on the query string', async () => {
		const { client, get } = clientReturning();
		await createGenerationsApi(client).getGenerationHistory({ origin: 'mcp', status: 'completed' });
		const url = String((get.mock.calls[0] as unknown[])[0]);
		expect(new URLSearchParams(url.split('?')[1]).get('origin')).toBe('mcp');
	});

	it('leaves it off when no source is chosen', async () => {
		const { client, get } = clientReturning();
		await createGenerationsApi(client).getGenerationHistory({ status: 'completed' });
		expect(String((get.mock.calls[0] as unknown[])[0])).not.toContain('origin');
	});
});
