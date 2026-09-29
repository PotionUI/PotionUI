import { describe, it, expect } from 'vitest';
import { render } from 'svelte/server';
import GenerationCard from './GenerationCard.svelte';

function card(over: Record<string, unknown>) {
	const generation = {
		id: 'g1',
		form_data: {},
		status: 'failed',
		progress: 0,
		created_at: '2026-09-29T10:00:00Z',
		updated_at: '2026-09-29T10:00:00Z',
		files: [],
		rating: 0,
		is_favorite: false,
		error_id: '01M3QERRORID',
		...over
	};
	return render(GenerationCard, { props: { generation } as any }).body;
}

const blocked = {
	error_code: 'content_blocked',
	error_message: 'Blocked by content policy.',
	error_user_message: 'Blocked by content policy.\n\n- Try a different prompt or seed\n- Ask your administrator about the content policy'
};

describe('GenerationCard failure rendering', () => {
	it('content_blocked shows a Blocked badge and no error id', () => {
		const out = card(blocked);
		expect(out).toContain('Blocked');
		expect(out).not.toContain('>failed<');
		expect(out).toContain('Try a different prompt or seed');
		expect(out).not.toContain('Ask your administrator');
		expect(out).not.toContain('Error ID');
		expect(out).not.toContain('Give this to your admin');
		expect(out).not.toContain('line-clamp');
	});

	it('banned_prompt shows no error id', () => {
		const out = card({ ...blocked, error_code: 'banned_prompt' });
		expect(out).not.toContain('Error ID');
	});

	it('content_check_unavailable keeps the error id', () => {
		const out = card({ ...blocked, error_code: 'content_check_unavailable' });
		expect(out).toContain('Blocked');
		expect(out).toContain('01M3QERRORID');
		expect(out).toContain('Give this to your admin');
	});

	it('ordinary failures keep the danger treatment, badge and error id', () => {
		const out = card({ error_code: 'cuda_oom', error_message: 'The GPU ran out of memory.', error_user_message: 'The GPU ran out of memory.\n\n- Try smaller' });
		expect(out).toContain('failed');
		expect(out).toContain('text-danger');
		expect(out).toContain('01M3QERRORID');
		expect(out).toContain('Give this to your admin');
		expect(out).not.toContain('line-clamp');
	});
});
