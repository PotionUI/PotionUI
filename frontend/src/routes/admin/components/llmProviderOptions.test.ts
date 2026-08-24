import { describe, expect, it } from 'vitest';
import { coerceProviderOptionText } from './llmProviderOptions';

describe('coerceProviderOptionText', () => {
	it.each(['5m', '1h', '30s', '1.5h', '10ms'])('keeps the duration %s verbatim', (input) => {
		expect(coerceProviderOptionText(input)).toBe(input);
	});

	it.each([
		['0', 0],
		['-1', -1],
		['300', 300],
		['0.5', 0.5],
		['-0.25', -0.25]
	])('coerces the numeric literal %s', (input, expected) => {
		expect(coerceProviderOptionText(input as string)).toBe(expected);
	});

	it('coerces zero to a number rather than falling back to the string', () => {
		expect(coerceProviderOptionText('0')).not.toBe('0');
	});

	it('tolerates surrounding whitespace on a numeric literal', () => {
		expect(coerceProviderOptionText('  42  ')).toBe(42);
	});

	it.each(['', '   '])('returns blank input unchanged (%p)', (input) => {
		expect(coerceProviderOptionText(input)).toBe(input);
	});

	it.each(['indefinite', 'Infinity', '-Infinity', 'NaN', 'm5'])(
		'leaves the non-numeric value %s as text',
		(input) => {
			expect(coerceProviderOptionText(input)).toBe(input);
		}
	);
});
