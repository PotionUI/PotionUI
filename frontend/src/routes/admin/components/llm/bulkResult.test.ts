import { describe, expect, it } from 'vitest';
import { bulkOutcomeMessage, summarizeBulkOutcome } from './bulkResult';

function settled(count: number): PromiseSettledResult<unknown>[] {
	return Array.from({ length: count }, () => ({ status: 'fulfilled', value: undefined }) as const);
}

function rejected(count: number): PromiseSettledResult<unknown>[] {
	return Array.from({ length: count }, () => ({ status: 'rejected', reason: new Error('nope') }) as const);
}

describe('summarizeBulkOutcome', () => {
	it('counts all-success results', () => {
		expect(summarizeBulkOutcome(settled(3))).toEqual({ total: 3, succeeded: 3, failed: 0 });
	});

	it('counts a mix of success and failure', () => {
		expect(summarizeBulkOutcome([...settled(2), ...rejected(1)])).toEqual({ total: 3, succeeded: 2, failed: 1 });
	});

	it('counts all-failure results', () => {
		expect(summarizeBulkOutcome(rejected(2))).toEqual({ total: 2, succeeded: 0, failed: 2 });
	});
});

describe('bulkOutcomeMessage', () => {
	it('reports a clean success with singular wording for one item', () => {
		expect(bulkOutcomeMessage({ total: 1, succeeded: 1, failed: 0 }, 'deleted', 'session')).toEqual({
			ok: true,
			text: '1 session deleted'
		});
	});

	it('reports a clean success with plural wording for many items', () => {
		expect(bulkOutcomeMessage({ total: 3, succeeded: 3, failed: 0 }, 'enabled', 'configuration')).toEqual({
			ok: true,
			text: '3 configurations enabled'
		});
	});

	it('reports a total failure', () => {
		expect(bulkOutcomeMessage({ total: 2, succeeded: 0, failed: 2 }, 'deleted', 'session')).toEqual({
			ok: false,
			text: '2 sessions could not be deleted'
		});
	});

	it('reports a partial failure', () => {
		expect(bulkOutcomeMessage({ total: 3, succeeded: 2, failed: 1 }, 'deleted', 'session')).toEqual({
			ok: false,
			text: '2 sessions deleted, 1 failed'
		});
	});

	it('returns an empty message when nothing was attempted', () => {
		expect(bulkOutcomeMessage({ total: 0, succeeded: 0, failed: 0 }, 'deleted', 'session')).toEqual({
			ok: true,
			text: ''
		});
	});
});
