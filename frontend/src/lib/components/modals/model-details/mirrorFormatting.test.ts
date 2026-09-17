import { describe, expect, it } from 'vitest';
import { formatMirrorIds } from './mirrorFormatting';

describe('formatMirrorIds', () => {
	it('joins model and version ids with a middle dot', () => {
		expect(formatMirrorIds('101055', '126601')).toBe('model 101055 · version 126601');
	});

	it('omits the version segment when there is no version id', () => {
		expect(formatMirrorIds('101055', null)).toBe('model 101055');
		expect(formatMirrorIds('101055', undefined)).toBe('model 101055');
	});

	it('returns an empty string when there is no model id', () => {
		expect(formatMirrorIds(null, null)).toBe('');
		expect(formatMirrorIds(undefined, '126601')).toBe('version 126601');
	});
});
