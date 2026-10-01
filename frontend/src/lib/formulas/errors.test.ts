import { describe, expect, it } from 'vitest';
import { formulaErrorMessage } from './errors';

describe('formulaErrorMessage', () => {
	it('prefers a string detail', () => {
		expect(formulaErrorMessage({ response: { data: { detail: 'Name taken' } } }, 'x')).toBe('Name taken');
	});

	it('reads the message inside a detail object', () => {
		expect(formulaErrorMessage({ response: { data: { detail: { error: 'e', message: 'Already exists' } } } }, 'x')).toBe('Already exists');
	});

	it('reads the error then the message of the body', () => {
		expect(formulaErrorMessage({ response: { data: { error: 'Bad' } } }, 'x')).toBe('Bad');
		expect(formulaErrorMessage({ response: { data: { message: 'Worse' } } }, 'x')).toBe('Worse');
	});

	it('falls back to the error message then the fallback text', () => {
		expect(formulaErrorMessage(new Error('Network down'), 'x')).toBe('Network down');
		expect(formulaErrorMessage({}, 'Could not save')).toBe('Could not save');
		expect(formulaErrorMessage(null, 'Could not save')).toBe('Could not save');
	});

	it('ignores a validation list detail', () => {
		expect(formulaErrorMessage({ response: { data: { detail: [{ msg: 'x' }] } }, message: 'Request failed' }, 'f')).toBe('Request failed');
	});
});
