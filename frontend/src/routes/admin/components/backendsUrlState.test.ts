import { describe, it, expect } from 'vitest';
import { readBackendsUrlState, writeBackendsUrlState } from './backendsUrlState';

describe('readBackendsUrlState', () => {
	it('reads backend and view from the query params', () => {
		const params = new URLSearchParams('backend=abc123&view=infrastructure');
		expect(readBackendsUrlState(params)).toEqual({ backendId: 'abc123', view: 'infrastructure' });
	});

	it('returns nulls when the params are absent', () => {
		const params = new URLSearchParams('');
		expect(readBackendsUrlState(params)).toEqual({ backendId: null, view: null });
	});

	it('passes an unknown view through unvalidated - the caller checks it against the driver', () => {
		const params = new URLSearchParams('backend=abc123&view=not-a-real-tab');
		expect(readBackendsUrlState(params)).toEqual({ backendId: 'abc123', view: 'not-a-real-tab' });
	});
});

describe('writeBackendsUrlState', () => {
	it('round-trips a selected backend and non-overview tab', () => {
		const url = new URL('https://example.test/admin?tab=backends');
		const next = writeBackendsUrlState(url, { backendId: 'abc123', view: 'infrastructure' });
		expect(readBackendsUrlState(next.searchParams)).toEqual({ backendId: 'abc123', view: 'infrastructure' });
		expect(next.searchParams.get('tab')).toBe('backends');
	});

	it('removes both params when nothing is selected', () => {
		const url = new URL('https://example.test/admin?tab=backends&backend=abc123&view=stats');
		const next = writeBackendsUrlState(url, { backendId: null, view: null });
		expect(next.searchParams.has('backend')).toBe(false);
		expect(next.searchParams.has('view')).toBe(false);
	});

	it('removes the view param when it is overview', () => {
		const url = new URL('https://example.test/admin?tab=backends&backend=abc123&view=stats');
		const next = writeBackendsUrlState(url, { backendId: 'abc123', view: 'overview' });
		expect(next.searchParams.get('backend')).toBe('abc123');
		expect(next.searchParams.has('view')).toBe(false);
	});

	it('does not mutate the input URL', () => {
		const url = new URL('https://example.test/admin?tab=backends');
		writeBackendsUrlState(url, { backendId: 'abc123', view: 'stats' });
		expect(url.searchParams.has('backend')).toBe(false);
	});
});
