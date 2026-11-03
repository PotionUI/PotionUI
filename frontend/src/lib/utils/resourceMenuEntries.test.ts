import { describe, expect, it } from 'vitest';
import type { PromptResourceSpec } from './promptResources';
import { bundleMenuEntries, resourceMenuEntries } from './resourceMenuEntries';

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

const bundleSpec: PromptResourceSpec = {
	field: 'bundles',
	kind: 'image',
	token: '<Picture @>',
	source: 'stack',
	source_label: 'Stack',
	source_badge: 'Stk'
};

describe('bundleMenuEntries', () => {
	it('lists each bundle by name with a kind badge', () => {
		const entries = bundleMenuEntries([{ model: 'model:01X', strength: 1 }, { model: 'model:01Y', strength: 1 }], bundleSpec, {
			stack: {
				'model:01X': { name: 'Face', members: [{ kind: 'image', name: null }] },
				'model:01Y': { name: 'Dance', members: [{ kind: 'image', name: null }, { kind: 'video', name: null }] }
			}
		});
		expect(entries.map((e) => [e.id, e.label, e.value])).toEqual([
			['model:01X', 'Face', 'Picture'],
			['model:01Y', 'Dance', 'Picture + Video']
		]);
	});

	it('shows the id and the source badge before the directory loads', () => {
		const [entry] = bundleMenuEntries([{ model: 'model:01X', strength: 1 }], bundleSpec, {});
		expect([entry.label, entry.value]).toEqual(['01X', 'Stk']);
	});

	it('reads only its own source directory', () => {
		const [entry] = bundleMenuEntries([{ model: 'model:01X', strength: 1 }], bundleSpec, {
			other: { 'model:01X': { name: 'Elsewhere', members: [{ kind: 'audio', name: null }] } }
		});
		expect([entry.label, entry.value]).toEqual(['01X', 'Stk']);
	});
});
