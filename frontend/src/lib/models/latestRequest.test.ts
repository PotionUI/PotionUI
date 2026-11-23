import { describe, expect, it } from 'vitest';
import { createLatestRequest } from './latestRequest';

describe('createLatestRequest', () => {
	it('treats only the newest ticket as current', () => {
		const guard = createLatestRequest();
		const first = guard.begin();
		const second = guard.begin();
		expect(guard.isCurrent(first)).toBe(false);
		expect(guard.isCurrent(second)).toBe(true);
	});

	it('invalidate drops an in-flight ticket', () => {
		const guard = createLatestRequest();
		const ticket = guard.begin();
		guard.invalidate();
		expect(guard.isCurrent(ticket)).toBe(false);
	});

	it('lets a slow earlier response lose to a later one', async () => {
		const guard = createLatestRequest();
		const applied: string[] = [];
		const run = async (value: string, delay: number) => {
			const ticket = guard.begin();
			await new Promise((resolve) => setTimeout(resolve, delay));
			if (guard.isCurrent(ticket)) applied.push(value);
		};
		await Promise.all([run('old', 20), run('new', 1)]);
		expect(applied).toEqual(['new']);
	});
});
