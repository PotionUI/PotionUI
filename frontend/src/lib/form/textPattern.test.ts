import { describe, it, expect } from 'vitest';
import { DEFAULT_PATTERN_MESSAGE, patternViolation } from './textPattern';

const ABC_PATTERN = '^(?=[\\s\\S]*^[ \\t]*X:)(?=[\\s\\S]*^[ \\t]*K:)';
const ABC_MESSAGE = 'ABC needs an X: (reference number) and a K: (key) header line';

describe('patternViolation', () => {
	it('accepts an ABC score with X: and K: header lines', () => {
		expect(patternViolation('X:1\nT:Song\nK:C\nCDEF|', ABC_PATTERN, ABC_MESSAGE)).toBeNull();
	});

	it.each([
		['C D E F | G A B c |'],
		['K:C\nCDEF|'],
		['X:1\nCDEF|'],
		['T:X: inline\nK:C']
	])('rejects %j with the declared message', (value) => {
		expect(patternViolation(value, ABC_PATTERN, ABC_MESSAGE)).toBe(ABC_MESSAGE);
	});

	it('skips empty values, missing patterns and invalid patterns', () => {
		expect(patternViolation('', ABC_PATTERN, ABC_MESSAGE)).toBeNull();
		expect(patternViolation('   ', ABC_PATTERN, ABC_MESSAGE)).toBeNull();
		expect(patternViolation('anything', undefined)).toBeNull();
		expect(patternViolation('anything', '(')).toBeNull();
	});

	it('falls back to a generic message', () => {
		expect(patternViolation('abc', '^\\d+$')).toBe(DEFAULT_PATTERN_MESSAGE);
	});
});
