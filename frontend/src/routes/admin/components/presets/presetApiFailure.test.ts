import { describe, it, expect } from 'vitest';
import { assignFieldErrors, presetApiFailure, presetResponseFailure, runPresetUninstall } from './presetApiFailure';

function axiosError(status: number, data: unknown) {
	return { isAxiosError: true, message: 'Request failed with status code ' + status, response: { status, data } };
}

const HAS_MIRRORS_BODY = {
	success: false,
	error: 'preset_has_mirrors',
	message: 'Remove its mirrors first.',
	mirrors: [
		{ preset_id: '01JD8A', name: 'Anime' },
		{ preset_id: '01JD8B', name: 'Film Covers' }
	]
};

describe('presetApiFailure', () => {
	it('reads the status, code, message and errors of a new-route refusal', () => {
		const failure = presetApiFailure(
			axiosError(400, {
				detail: { success: false, error: 'invalid_display', message: 'Invalid display.', errors: ['name must be 1-80 characters'] }
			}),
			'fallback'
		);
		expect(failure).toEqual({
			status: 400,
			code: 'invalid_display',
			message: 'Invalid display.',
			errors: ['name must be 1-80 characters'],
			mirrors: []
		});
	});

	it('reads the mirror list of a 409 preset_has_mirrors', () => {
		const failure = presetApiFailure(axiosError(409, { detail: HAS_MIRRORS_BODY }), 'fallback');
		expect(failure.status).toBe(409);
		expect(failure.code).toBe('preset_has_mirrors');
		expect(failure.mirrors).toEqual(HAS_MIRRORS_BODY.mirrors);
	});

	it('also reads a refusal at the top level of the body', () => {
		const failure = presetApiFailure(axiosError(404, { success: false, error: 'preset_not_found', message: 'No such preset.' }), 'fallback');
		expect(failure.code).toBe('preset_not_found');
		expect(failure.message).toBe('No such preset.');
	});

	it('names a mirror by its id when the body has no name', () => {
		const failure = presetApiFailure(
			axiosError(409, { detail: { ...HAS_MIRRORS_BODY, mirrors: [{ preset_id: 'x1' }, { name: 'no id' }] } }),
			'fallback'
		);
		expect(failure.mirrors).toEqual([{ preset_id: 'x1', name: 'x1' }]);
	});

	it('falls back to the given message for a network error', () => {
		const failure = presetApiFailure(new TypeError(''), 'Could not save');
		expect(failure).toEqual({ status: null, code: null, message: 'Could not save', errors: [], mirrors: [] });
	});
});

describe('presetResponseFailure', () => {
	it('reads a legacy 200 success:false body', () => {
		expect(presetResponseFailure({ success: false, error: 'preset_in_use', message: 'In use.' }, 'fallback')).toMatchObject({
			status: null,
			code: 'preset_in_use',
			message: 'In use.'
		});
	});
});

describe('runPresetUninstall', () => {
	it('reports a plain uninstall as removed', async () => {
		const outcome = await runPresetUninstall(async () => ({ success: true, message: 'ok' }), 'fallback');
		expect(outcome).toEqual({ kind: 'removed', tombstone: null });
	});

	it('carries the tombstone of a removed mirror', async () => {
		const outcome = await runPresetUninstall(
			async () => ({ success: true, data: { tombstone: { preset_id: '01JD8A', name: 'Anime' } } }),
			'fallback'
		);
		expect(outcome).toEqual({ kind: 'removed', tombstone: { preset_id: '01JD8A', name: 'Anime' } });
	});

	it('turns an HTTP 409 preset_has_mirrors into the blocking list', async () => {
		const outcome = await runPresetUninstall(async () => {
			throw axiosError(409, { detail: HAS_MIRRORS_BODY });
		}, 'fallback');
		expect(outcome).toEqual({ kind: 'has_mirrors', mirrors: HAS_MIRRORS_BODY.mirrors, message: 'Remove its mirrors first.' });
	});

	it('also accepts preset_has_mirrors on a 200 success:false body', async () => {
		const outcome = await runPresetUninstall(async () => HAS_MIRRORS_BODY, 'fallback');
		expect(outcome.kind).toBe('has_mirrors');
	});

	it('keeps an older 200 success:false refusal as a plain refusal', async () => {
		const outcome = await runPresetUninstall(
			async () => ({ success: false, error: 'preset_in_use', message: 'A recipe still needs it.' }),
			'fallback'
		);
		expect(outcome).toEqual({ kind: 'refused', message: 'A recipe still needs it.' });
	});

	it('turns any other HTTP error into a plain refusal', async () => {
		const outcome = await runPresetUninstall(async () => {
			throw axiosError(500, { detail: { success: false, error: 'boom', message: 'Server broke.' } });
		}, 'fallback');
		expect(outcome).toEqual({ kind: 'refused', message: 'Server broke.' });
	});
});

describe('assignFieldErrors', () => {
	it('puts each error next to the field it names', () => {
		const result = assignFieldErrors(
			['prefix: unbalanced braces', 'Negative is too long', 'mode "edit" is not offered', 'something else'],
			['prefix', 'suffix', 'negative', 'modes'] as const
		);
		expect(result.byField).toEqual({
			prefix: 'prefix: unbalanced braces',
			negative: 'Negative is too long',
			modes: 'mode "edit" is not offered'
		});
		expect(result.general).toEqual(['something else']);
	});

	it('matches a field by its singular or plural name', () => {
		expect(assignFieldErrors(['modes must not be empty'], ['modes'] as const).byField).toEqual({
			modes: 'modes must not be empty'
		});
		expect(assignFieldErrors(['names are limited'], ['name'] as const).byField).toEqual({ name: 'names are limited' });
	});

	it('does not match a field name inside another word', () => {
		const result = assignFieldErrors(['rename failed'], ['name'] as const);
		expect(result.byField).toEqual({});
		expect(result.general).toEqual(['rename failed']);
	});
});
