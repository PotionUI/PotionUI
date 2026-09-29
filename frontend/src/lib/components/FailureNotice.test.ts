import { describe, it, expect } from 'vitest';
import { render } from 'svelte/server';
import FailureNotice from './FailureNotice.svelte';
import { isContentPolicyCode, policyShowsErrorId, firstHintLine } from '$lib/generation/failurePolicy';

const html = (props: Record<string, unknown>) => render(FailureNotice, { props: props as any }).body;

describe('policy helpers', () => {
	it('classifies the three content-policy codes', () => {
		for (const code of ['banned_prompt', 'content_blocked', 'content_check_unavailable']) {
			expect(isContentPolicyCode(code)).toBe(true);
		}
		expect(isContentPolicyCode('cuda_oom')).toBe(false);
		expect(isContentPolicyCode(null)).toBe(false);
	});

	it('keeps the error id only where the admin is needed', () => {
		expect(policyShowsErrorId('content_blocked')).toBe(false);
		expect(policyShowsErrorId('banned_prompt')).toBe(false);
		expect(policyShowsErrorId('content_check_unavailable')).toBe(true);
		expect(policyShowsErrorId('cuda_oom')).toBe(true);
		expect(policyShowsErrorId(null)).toBe(true);
	});

	it('extracts the first hint line from the composed user message', () => {
		const user = 'Blocked by content policy.\n\n- Try a different prompt or seed\n- Ask your administrator';
		expect(firstHintLine(user, 'Blocked by content policy.')).toBe('Try a different prompt or seed');
		expect(firstHintLine(null, null)).toBe('');
	});
});

describe('FailureNotice', () => {
	it('renders a calm notice without error id or admin line', () => {
		const out = html({ message: 'Blocked by content policy.', hint: 'Try a different prompt or seed' });
		expect(out).toContain('Blocked by content policy.');
		expect(out).toContain('Try a different prompt or seed');
		expect(out).toContain('text-warning');
		expect(out).not.toContain('text-danger');
		expect(out).not.toContain('Error ID');
		expect(out).not.toContain('Give this to your admin');
	});

	it('keeps the error id when one is passed', () => {
		const out = html({ message: 'Content check unavailable.', hint: 'Ask your administrator', errorId: '01M3Q' });
		expect(out).toContain('Error ID');
		expect(out).toContain('01M3Q');
		expect(out).toContain('Give this to your admin');
	});

	it('never clips or truncates and has no ellipsis line', () => {
		const out = html({ message: 'Blocked by content policy.', hint: 'Try a different prompt or seed' });
		expect(out).not.toMatch(/line-clamp|overflow-hidden|truncate|max-h-/);
		expect(out).not.toContain('...');
		expect(out).not.toContain('…');
	});
});
