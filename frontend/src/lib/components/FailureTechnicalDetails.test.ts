import { describe, it, expect } from 'vitest';
import { render } from 'svelte/server';
import FailureTechnicalDetails from './FailureTechnicalDetails.svelte';

const html = (props: Record<string, unknown>) => render(FailureTechnicalDetails, { props: props as any }).body;

describe('FailureTechnicalDetails', () => {
	it('renders a collapsed Technical details block with the detail when one is passed', () => {
		const out = html({ detail: 'RuntimeError: boom\n  File "/srv/app/pipe.py", line 12' });
		expect(out).toContain('data-failure-technical-details');
		expect(out).toContain('Technical details');
		expect(out).toContain('RuntimeError: boom');
		expect(out).toContain('/srv/app/pipe.py');
		expect(out).not.toMatch(/<details[^>]*\bopen\b/);
	});

	it('renders nothing at all without a detail', () => {
		for (const props of [{}, { detail: null }, { detail: '' }]) {
			const out = html(props);
			expect(out).not.toContain('Technical details');
			expect(out).not.toContain('<details');
			expect(out).not.toContain('<pre');
		}
	});
});
