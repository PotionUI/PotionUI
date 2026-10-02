import { describe, expect, it } from 'vitest';
import { resourceMenuEntries } from './resourceMenuEntries';

describe('resourceMenuEntries', () => {
	it('labels entries by file name so no two items share a picture number', () => {
		const entries = resourceMenuEntries(
			[
				{ path: '/pool/0.png', name: '0.png', url: '/u/0' },
				{ path: '/pool/0-edit.png', name: '0-edit.png', url: '/u/1' }
			],
			'image'
		);
		expect(entries.map((e) => e.label)).toEqual(['0.png', '0-edit.png']);
		expect(entries.every((e) => !/\d/.test(e.value))).toBe(true);
	});

	it('falls back to the last path segment when an item has no name', () => {
		const entries = resourceMenuEntries(['/pool/sub/a.png'], 'image');
		expect(entries[0].label).toBe('a.png');
		expect(entries[0].value).toBe('Picture');
	});
});
