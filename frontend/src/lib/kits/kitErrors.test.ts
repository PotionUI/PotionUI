import { describe, expect, it } from 'vitest';
import { isNameClash, kitErrorCode, kitErrorMessage, kitErrorSuggestion } from './kitErrors';

const conflict = {
	response: {
		status: 409,
		data: { detail: { success: false, error: 'item_path_taken', message: "'face' is already used, try 'face2'", suggestion: 'face2' } }
	}
};

describe('kitErrorMessage', () => {
	it('reads the message inside the HTTP error detail', () => {
		expect(kitErrorMessage(conflict, 'x')).toBe("'face' is already used, try 'face2'");
	});

	it('reads an envelope that failed without throwing', () => {
		expect(kitErrorMessage({ success: false, message: 'Kit is full' }, 'x')).toBe('Kit is full');
	});

	it('never shows a bare error code', () => {
		expect(kitErrorMessage({ success: false, error: 'kit_full' }, 'Could not save')).toBe('Could not save');
	});

	it('falls back', () => {
		expect(kitErrorMessage(null, 'Could not save')).toBe('Could not save');
		expect(kitErrorMessage({}, 'Could not save')).toBe('Could not save');
	});
});

describe('code and suggestion', () => {
	it('pulls them out of the detail', () => {
		expect(kitErrorCode(conflict)).toBe('item_path_taken');
		expect(kitErrorSuggestion(conflict)).toBe('face2');
		expect(kitErrorSuggestion({})).toBeNull();
	});
});

describe('isNameClash', () => {
	it('reads a 409 and a path_taken code', () => {
		expect(isNameClash(conflict)).toBe(true);
		expect(isNameClash({ success: false, error: 'kit_path_taken' })).toBe(true);
		expect(isNameClash({ response: { status: 404, data: { detail: { error: 'kit_not_found' } } } })).toBe(false);
	});
});
