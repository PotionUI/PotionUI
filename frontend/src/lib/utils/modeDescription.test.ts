import { describe, it, expect } from 'vitest';
import { modeDescription } from './modeDescription';

describe('modeDescription', () => {
	it('prefers the short description', () => {
		expect(modeDescription({ short_description: 'Short', description: 'Long paragraph' })).toBe('Short');
	});
	it('falls back to the long description', () => {
		expect(modeDescription({ short_description: null, description: 'Long paragraph' })).toBe('Long paragraph');
		expect(modeDescription({ short_description: '  ', description: 'Long paragraph' })).toBe('Long paragraph');
	});
	it('returns null when neither exists', () => {
		expect(modeDescription({})).toBeNull();
		expect(modeDescription({ short_description: '', description: '' })).toBeNull();
	});
});
