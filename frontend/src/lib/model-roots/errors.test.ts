import { describe, it, expect } from 'vitest';
import { modelRootsErrorMessage, modelRootsErrorCode } from './errors';

function axiosError(detail: unknown) {
	return { response: { data: { detail } } };
}

describe('modelRootsErrorMessage', () => {
	it('reads the message off an error_response-shaped detail object', () => {
		const error = axiosError({ error: 'model_roots_offline', message: "Root 'x' is offline" });

		expect(modelRootsErrorMessage(error, 'fallback')).toBe("Root 'x' is offline");
	});

	it('reads a top-level response message', () => {
		const error = { response: { data: { success: false, message: 'The scope could not be read.' } } };

		expect(modelRootsErrorMessage(error, 'fallback')).toBe('The scope could not be read.');
	});

	it('falls back to a plain string detail', () => {
		const error = axiosError('not found');

		expect(modelRootsErrorMessage(error, 'fallback')).toBe('not found');
	});

	it('falls back to error.message when there is no detail', () => {
		const error = { message: 'Network Error' };

		expect(modelRootsErrorMessage(error, 'fallback')).toBe('Network Error');
	});

	it('falls back to the provided fallback when nothing else is available', () => {
		expect(modelRootsErrorMessage({}, 'fallback')).toBe('fallback');
		expect(modelRootsErrorMessage(null, 'fallback')).toBe('fallback');
	});
});

describe('modelRootsErrorCode', () => {
	it('reads the error code off the detail object', () => {
		const error = axiosError({ error: 'model_roots_home_protected', message: 'nope' });

		expect(modelRootsErrorCode(error)).toBe('model_roots_home_protected');
	});

	it('returns null when there is no structured detail', () => {
		expect(modelRootsErrorCode(axiosError('plain string'))).toBeNull();
		expect(modelRootsErrorCode({})).toBeNull();
		expect(modelRootsErrorCode(null)).toBeNull();
	});
});
