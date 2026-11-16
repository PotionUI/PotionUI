import { describe, it, expect } from 'vitest';
import { describeMediaToolFailure, parseMediaToolFailure } from './errors';

describe('parseMediaToolFailure', () => {
	it('reads the structured refusal out of a raised HTTP error', () => {
		const error = {
			message: 'Request failed with status code 400',
			response: {
				status: 400,
				data: {
					detail: { error: 'invalid_params', message: 'speed must be at most 4', field: 'speed', step_index: 0 }
				}
			}
		};
		expect(parseMediaToolFailure(error)).toEqual({
			code: 'invalid_params',
			message: 'speed must be at most 4',
			field: 'speed',
			stepIndex: 0
		});
	});

	it('reads a flat body as well', () => {
		const error = { response: { data: { error: 'invalid_params', message: 'Nope', field: 'end' } } };
		expect(parseMediaToolFailure(error)).toMatchObject({ code: 'invalid_params', message: 'Nope', field: 'end' });
	});

	it('words a refusal that carries a code but no message', () => {
		const error = { message: 'Request failed with status code 400', response: { data: { detail: { error: 'replace_not_allowed' } } } };
		expect(describeMediaToolFailure(error)).toBe('This edit can only be saved as a new item.');
	});

	it('never shows axios own status line when there is anything better', () => {
		const error = { message: 'Request failed with status code 400', response: { data: { detail: { error: 'unsupported_media' } } } };
		expect(describeMediaToolFailure(error)).not.toContain('status code');
	});

	it('uses the plan refusal text for a quota refusal', () => {
		const error = {
			response: {
				data: {
					detail: {
						error: 'limit_exceeded',
						kind: 'upload_file_size',
						code: 'upload_file_size_exceeded',
						message: 'raw',
						limit: 1,
						used: 2
					}
				}
			}
		};
		expect(describeMediaToolFailure(error).length).toBeGreaterThan(0);
	});

	it('falls back to a plain thrown error and then to a default', () => {
		expect(describeMediaToolFailure(new Error('Offline'))).toBe('Offline');
		expect(describeMediaToolFailure(null)).toBe('The edit could not be applied');
		expect(parseMediaToolFailure({})).toMatchObject({ code: null, field: null, stepIndex: null });
	});
});

describe('describeMediaToolFailure precedence', () => {
	const refusal = {
		message: 'Request failed with status code 400',
		response: {
			status: 400,
			data: {
				detail: {
					success: false,
					error: 'unsupported_media',
					message: 'Crop to subject needs an image with transparency',
					step_index: 0
				}
			}
		}
	};

	it('shows the server sentence rather than the generic one for its code', () => {
		expect(describeMediaToolFailure(refusal)).toBe('Crop to subject needs an image with transparency');
	});

	it('keeps the message of an error thrown by local code', () => {
		expect(describeMediaToolFailure(new Error('The mask could not be saved'), 'The preview could not be drawn.')).toBe('The mask could not be saved');
	});

	it('uses the caller fallback when the failure carries nothing readable', () => {
		expect(describeMediaToolFailure({}, 'The preview could not be drawn.')).toBe('The preview could not be drawn.');
		expect(describeMediaToolFailure(undefined)).toBe('The edit could not be applied');
	});
});
