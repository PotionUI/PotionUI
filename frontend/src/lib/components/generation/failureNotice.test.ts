import { describe, expect, it } from 'vitest';
import { failureNotice } from './failureNotice';

const RAW = 'RuntimeError: failed in /srv/models/private/unet.safetensors';

describe('failureNotice', () => {
	it.each(['completed', 'running', 'pending', undefined])('is absent for %s', (status) => {
		expect(failureNotice({ status, error_message: RAW }, true)).toBeNull();
	});

	it('shows an admin the stored message and the error id', () => {
		expect(failureNotice({ status: 'failed', error_message: RAW, error_id: 'E-1', error_contact_admin: false }, true)).toEqual({
			title: 'Failed',
			message: RAW,
			errorId: 'E-1',
			contactAdmin: false,
			copyText: RAW
		});
	});

	it('prefers the user message over the raw one', () => {
		const notice = failureNotice({ status: 'failed', error_message: 'short', error_user_message: 'short plus hint' }, true);
		expect(notice?.message).toBe('short plus hint');
	});

	it('shows a regular user the message the server already scoped for them', () => {
		const notice = failureNotice(
			{ status: 'failed', error_message: 'Out of memory.', error_user_message: 'Out of memory. Try a smaller size.', error_contact_admin: true, error_id: 'E-2' },
			false
		);
		expect(notice).toMatchObject({ message: 'Out of memory. Try a smaller size.', errorId: 'E-2', contactAdmin: true });
	});

	it('never shows a regular user a message the server did not scope', () => {
		const notice = failureNotice({ status: 'failed', error_message: RAW, error_id: 'E-3' }, false);
		expect(notice).toEqual({
			title: 'Failed',
			message: 'This generation failed.',
			errorId: null,
			contactAdmin: false,
			copyText: 'This generation failed.'
		});
		expect(JSON.stringify(notice)).not.toContain('/srv/');
	});

	it('words a cancelled generation without a message', () => {
		expect(failureNotice({ status: 'cancelled' }, false)).toMatchObject({
			title: 'Cancelled',
			message: 'This generation was cancelled.'
		});
		expect(failureNotice({ status: 'cancelled' }, true)).toMatchObject({ title: 'Cancelled' });
	});

	it('falls back for an admin when nothing was stored', () => {
		expect(failureNotice({ status: 'failed' }, true)?.message).toBe('This generation failed.');
	});
});
