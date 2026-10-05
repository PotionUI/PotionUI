import { describe, expect, it } from 'vitest';
import { formatValue } from './format';

const resolution = {
	type: 'resolution',
	options: [
		{ value: '1344x768', description: 'Landscape' },
		{ value: '1024x1024', label: 'Square 1K' }
	]
};

describe('formatValue for resolution', () => {
	it('shows width by height for an option without a label', () => {
		expect(formatValue(resolution, '1344x768')).toBe('1344 × 768');
	});

	it('shows width by height for a value outside the options', () => {
		expect(formatValue(resolution, '640x480')).toBe('640 × 480');
	});

	it('prefers the option label when there is one', () => {
		expect(formatValue(resolution, '1024x1024')).toBe('Square 1K');
	});

	it('shows width by height for an object value', () => {
		expect(formatValue(resolution, { width: 832, height: 1216 })).toBe('832 × 1216');
	});
});
