import { describe, expect, it } from 'vitest';
import type { PromptResourceSpec } from './promptResources';
import {
	citeableRefModRows,
	isRefModRow,
	refModBadge,
	refModIds,
	refModName,
	refModPrimaryKind
} from './refMods';

const specs: PromptResourceSpec[] = [
	{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' },
	{ field: 'reference_mods', kind: 'image', label: 'RefMods', token: '<Picture @>', source: 'refmod' },
	{ field: 'reference_mods', kind: 'video', label: 'RefMods', token: '<Video @>', source: 'refmod' }
];

describe('refMods', () => {
	it('drops rows whose strength is zero or negative', () => {
		const rows = citeableRefModRows([
			{ model: 'model:a', strength: 1, copies: 1 },
			{ model: 'model:b', strength: 0 },
			{ model: 'model:c', strength: -0.5 },
			{ model: 'model:d' }
		]);
		expect(rows.map((r) => r.model)).toEqual(['model:a', 'model:d']);
	});

	it('does not mistake a media item for a refmod row', () => {
		expect(isRefModRow({ model: 'model:a', strength: 1 })).toBe(true);
		expect(isRefModRow({ path: '/a.png' })).toBe(false);
	});

	it('collects ids only from refmod fields', () => {
		const ids = refModIds(specs, {
			references: [{ path: '/a.png' }],
			reference_mods: [{ model: 'model:01X', strength: 1 }, { model: 'model:01Y', strength: 0 }]
		});
		expect(ids).toEqual(['01X']);
	});

	it('badges by member kinds and falls back while unknown', () => {
		expect(refModBadge(undefined)).toBe('RefMod');
		expect(refModBadge({ name: 'x', members: [{ kind: 'image', name: null }] })).toBe('Picture');
		expect(refModBadge({ name: 'x', members: [{ kind: 'video', name: null }, { kind: 'image', name: null }] })).toBe(
			'Picture + Video'
		);
		expect(refModPrimaryKind({ name: 'x', members: [{ kind: 'audio', name: null }] })).toBe('audio');
	});

	it('names by directory and falls back to the id', () => {
		expect(refModName('model:01X', { '01X': { name: 'Face', members: null } })).toBe('Face');
		expect(refModName('model:01X', {})).toBe('01X');
	});
});
