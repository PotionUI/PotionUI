import { describe, expect, it } from 'vitest';
import { isFullWidth } from './fieldWidth';

describe('isFullWidth', () => {
	it('is true only for a literal top-level full_width: true', () => {
		expect(isFullWidth({ full_width: true })).toBe(true);
	});

	it.each([
		['absent', {}],
		['false', { full_width: false }],
		['truthy non-boolean', { full_width: 1 }],
		['truthy string', { full_width: 'true' }],
		['nested under configuration (old shape, no longer read)', { configuration: { full_width: true } }],
		['null config', null],
		['undefined config', undefined],
		['non-object config', 'full_width']
	])('is false for %s', (_label, config) => {
		expect(isFullWidth(config)).toBe(false);
	});
});
