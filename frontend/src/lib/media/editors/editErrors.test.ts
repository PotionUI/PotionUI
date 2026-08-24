import { describe, it, expect } from 'vitest';
import { describeEditFailure } from './editErrors';

describe('describeEditFailure', () => {
	it('digs the refusal out of the raised HTTPException body', () => {
		const rejection = {
			message: 'Request failed with status code 400',
			response: {
				data: {
					detail: {
						error: 'invalid_edit',
						message: 'The crop rectangle (0,0 900x900) does not fit inside 512x512'
					}
				}
			}
		};
		expect(describeEditFailure(rejection)).toBe(
			'The crop rectangle (0,0 900x900) does not fit inside 512x512'
		);
	});

	it('prefers the server reason over axios own message', () => {
		const rejection = {
			message: 'Request failed with status code 400',
			response: { data: { detail: { message: 'Only one trim can be applied at a time' } } }
		};
		expect(describeEditFailure(rejection)).not.toContain('status code');
	});

	it('accepts a bare string detail', () => {
		expect(describeEditFailure({ response: { data: { detail: 'Not Found' } } })).toBe('Not Found');
	});

	it('falls back to a plain body message', () => {
		expect(describeEditFailure({ response: { data: { message: 'Nope' } } })).toBe('Nope');
	});

	it('reads a thrown Error', () => {
		expect(describeEditFailure(new Error('The clip is not loaded yet'))).toBe(
			'The clip is not loaded yet'
		);
	});

	it('falls back when there is nothing to read', () => {
		expect(describeEditFailure(null)).toBe('The edit could not be applied');
		expect(describeEditFailure({}, 'Custom')).toBe('Custom');
		expect(describeEditFailure({ response: { data: { detail: { message: '  ' } } } })).toBe(
			'The edit could not be applied'
		);
	});
});
