import { describe, it, expect } from 'vitest';
import { readAddBackendDriver, readBackendsUrlState, writeBackendsUrlState } from './backendsUrlState';

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

describe('writeBackendsUrlState catalog filters', () => {
	const filtered = 'https://example.test/admin?tab=backends&backend=abc&view=catalog&q=veo&task=txt2video&output=video&enabled=1';

	it('keeps the catalog filters while the same backend stays on the Catalog tab', () => {
		const next = writeBackendsUrlState(new URL(filtered), { backendId: 'abc', view: 'catalog' });
		expect(next.searchParams.get('task')).toBe('txt2video');
		expect(next.searchParams.get('q')).toBe('veo');
		expect(next.searchParams.get('enabled')).toBe('1');
	});

	it('drops them when leaving the Catalog tab', () => {
		const next = writeBackendsUrlState(new URL(filtered), { backendId: 'abc', view: 'stats' });
		for (const key of ['q', 'task', 'output', 'enabled']) expect(next.searchParams.has(key)).toBe(false);
		expect(next.searchParams.get('tab')).toBe('backends');
	});

	it('drops them when another backend is selected', () => {
		const next = writeBackendsUrlState(new URL(filtered), { backendId: 'other', view: 'catalog' });
		expect(next.searchParams.has('task')).toBe(false);
	});

	it('drops them when nothing is selected', () => {
		const next = writeBackendsUrlState(new URL(filtered), { backendId: null, view: null });
		expect(next.searchParams.has('q')).toBe(false);
	});
});

describe('add backend intent', () => {
	it('reads the driver to open Add backend with', () => {
		expect(readAddBackendDriver(new URLSearchParams('tab=backends&add=cloud.fake'))).toBe('cloud.fake');
		expect(readAddBackendDriver(new URLSearchParams('tab=backends&add='))).toBeNull();
		expect(readAddBackendDriver(new URLSearchParams('tab=backends'))).toBeNull();
	});

	it('is dropped from the URL once the tab writes its own state', () => {
		const url = new URL('https://example.test/admin?tab=backends&add=cloud.fake');
		const next = writeBackendsUrlState(url, { backendId: null, view: null });
		expect(next.searchParams.has('add')).toBe(false);
		expect(next.searchParams.get('tab')).toBe('backends');
	});
});
