import { describe, expect, it } from 'vitest';
import { refModMenuEntries, resourceMenuEntries } from './resourceMenuEntries';

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

describe('refModMenuEntries', () => {
	it('lists each refmod by name with a kind badge', () => {
		const entries = refModMenuEntries(
			[{ model: 'model:01X', strength: 1 }, { model: 'model:01Y', strength: 1 }],
			{
				'01X': { name: 'Face', members: [{ kind: 'image', name: null }] },
				'01Y': { name: 'Dance', members: [{ kind: 'image', name: null }, { kind: 'video', name: null }] }
			}
		);
		expect(entries.map((e) => [e.id, e.label, e.value])).toEqual([
			['model:01X', 'Face', 'Picture'],
			['model:01Y', 'Dance', 'Picture + Video']
		]);
	});

	it('shows the id and a generic badge before the directory loads', () => {
		const [entry] = refModMenuEntries([{ model: 'model:01X', strength: 1 }], {});
		expect([entry.label, entry.value]).toEqual(['01X', 'RefMod']);
	});
});
