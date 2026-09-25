import { describe, it, expect } from 'vitest';
import { generationFailureErrorId, generationFailureToastMessage } from './failureToast';

function notification(overrides: Partial<{ type: string; message: string; metadata: Record<string, unknown> | null }> = {}) {
	return {
		type: 'generation.failed',
		message: 'The GPU ran out of memory.',
		metadata: { error_id: 'gen-1', hint: '- Try a smaller resolution', error_code: 'cuda_oom' },
		...overrides
	};
}

describe('generationFailureErrorId', () => {
	it('reads the error id for a generation.failed notification', () => {
		expect(generationFailureErrorId(notification())).toBe('gen-1');
	});

	it('is null for any other notification type, even with an error_id-shaped metadata key', () => {
		expect(generationFailureErrorId(notification({ type: 'system.plugins' }))).toBeNull();
	});

	it('is null when metadata carries no error_id', () => {
		expect(generationFailureErrorId(notification({ metadata: {} }))).toBeNull();
		expect(generationFailureErrorId(notification({ metadata: null }))).toBeNull();
	});
});

describe('generationFailureToastMessage', () => {
	it('composes the safe message, the hint and a hand-to-admin line', () => {
		expect(generationFailureToastMessage(notification())).toBe(
			'The GPU ran out of memory.\n\n- Try a smaller resolution\n\nGive this to your admin.'
		);
	});

	it('never mentions handing anything to an admin when there is no error id', () => {
		expect(generationFailureToastMessage(notification({ metadata: { hint: '- Try again' } }))).toBe(
			'The GPU ran out of memory.\n\n- Try again'
		);
	});

	it('drops the hint line when metadata carries none', () => {
		expect(generationFailureToastMessage(notification({ metadata: { error_id: 'gen-2' } }))).toBe(
			'The GPU ran out of memory.\n\nGive this to your admin.'
		);
	});

	it('passes through the plain message for a non-failure notification', () => {
		expect(generationFailureToastMessage(notification({ type: 'generation.completed', metadata: {} }))).toBe(
			'The GPU ran out of memory.'
		);
	});
});
