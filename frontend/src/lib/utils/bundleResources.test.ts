import { describe, expect, it } from 'vitest';
import type { PromptResourceSpec } from './promptResources';
import {
	bundleBadge,
	bundleInfo,
	bundleName,
	bundlePrimaryKind,
	bundleRefs,
	bundleSourceLabel,
	citeableBundleRows,
	isBundleRow,
	isBundleSpec
} from './bundleResources';

const stack: PromptResourceSpec = {
	field: 'stacks',
	kind: 'image',
	label: 'Stacks',
	token: '<Picture @>',
	source: 'stack',
	source_label: 'Stack',
	source_badge: 'Stk'
};

const specs: PromptResourceSpec[] = [
	{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' },
	stack,
	{ ...stack, kind: 'video', token: '<Video @>' },
	{ field: 'casts', kind: 'image', token: '<Picture @>', source: 'cast' }
];

describe('bundleResources', () => {
	it('treats any spec with a source as a bundle spec', () => {
		expect(isBundleSpec(stack)).toBe(true);
		expect(isBundleSpec(specs[0])).toBe(false);
		expect(isBundleSpec(null)).toBe(false);
	});

	it('drops rows whose strength is zero or negative', () => {
		const rows = citeableBundleRows([
			{ model: 'model:a', strength: 1, copies: 1 },
			{ model: 'model:b', strength: 0 },
			{ model: 'model:c', strength: -0.5 },
			{ model: 'model:d' }
		]);
		expect(rows.map((r) => r.model)).toEqual(['model:a', 'model:d']);
	});

	it('does not mistake a media item for a bundle row', () => {
		expect(isBundleRow({ model: 'model:a', strength: 1 })).toBe(true);
		expect(isBundleRow({ path: '/a.png' })).toBe(false);
	});

	it('collects each citeable row once per source from bundle fields only', () => {
		const refs = bundleRefs(specs, {
			references: [{ path: '/a.png' }],
			stacks: [{ model: 'model:01X', strength: 1 }, { model: 'model:01Y', strength: 0 }, { model: 'model:01X' }],
			casts: [{ model: 'model:01X' }]
		});
		expect(refs).toEqual([
			{ source: 'stack', itemKey: 'model:01X' },
			{ source: 'cast', itemKey: 'model:01X' }
		]);
	});

	it('badges by member kinds and falls back to the source badge while unknown', () => {
		expect(bundleBadge(undefined, stack)).toBe('Stk');
		expect(bundleBadge(undefined, { ...stack, source_badge: undefined })).toBe('Stack');
		expect(bundleBadge(undefined, { ...stack, source_badge: undefined, source_label: undefined })).toBe('stack');
		expect(bundleBadge({ name: 'x', members: [{ kind: 'image', name: null }] }, stack)).toBe('Picture');
		expect(bundleBadge({ name: 'x', members: [{ kind: 'video', name: null }, { kind: 'image', name: null }] }, stack)).toBe(
			'Picture + Video'
		);
		expect(bundlePrimaryKind({ name: 'x', members: [{ kind: 'audio', name: null }] })).toBe('audio');
	});

	it('looks items up in their own source directory and names them, falling back to the id', () => {
		const directories = { stack: { 'model:01X': { name: 'Face', members: null } } };
		expect(bundleName('model:01X', bundleInfo(directories, stack, 'model:01X'))).toBe('Face');
		expect(bundleInfo(directories, specs[3], 'model:01X')).toBeUndefined();
		expect(bundleName('model:01X', undefined)).toBe('01X');
	});

	it('labels by the served source label, else the source id', () => {
		expect(bundleSourceLabel(stack)).toBe('Stack');
		expect(bundleSourceLabel({ ...stack, source_label: undefined })).toBe('stack');
	});
});
