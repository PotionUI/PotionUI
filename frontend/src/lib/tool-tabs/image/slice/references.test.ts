import { describe, expect, it } from 'vitest';
import type { GenerationTab } from '$lib/types/tabs';
import { referenceFieldsOf, referencePatch, referenceTargetsFor } from './references';

const schema = {
	properties: {
		generation: {
			type: 'group',
			children: [
				{ name: 'image', type: 'image', title: 'Init image' },
				{ name: 'refs', type: 'image', title: 'References', multiple: true, max_items: 8 },
				{ name: 'clips', type: 'media', title: 'Clips', multiple: true, accept: ['video'] }
			]
		}
	}
};

const tab = (id: string, formData: Record<string, unknown>) => ({ id, name: id, selectedPreset: 'krea2', formData }) as unknown as GenerationTab;

describe('reference targets', () => {
	it('keeps only multi-image fields', () => {
		expect(referenceFieldsOf(schema)).toEqual([{ name: 'refs', title: 'References', max: 8 }]);
	});

	it('shows free slots and what a short field takes', () => {
		const fields = { a: referenceFieldsOf(schema), b: referenceFieldsOf(schema), c: referenceFieldsOf(schema) };
		const targets = referenceTargetsFor([tab('a', { refs: [1, 2, 3] }), tab('b', { refs: [1, 2, 3, 4, 5, 6] }), tab('c', { refs: [1, 2, 3, 4, 5, 6, 7, 8] })], fields, { krea2: 'Krea 2' }, 5);
		expect(targets.map((t) => [t.id, t.detail, t.takes])).toEqual([
			['a::refs', 'Krea 2 · References · 5 of 8 slots free', 5],
			['b::refs', 'Krea 2 · References · 2 of 8 slots free, takes 01–02', 2]
		]);
	});

	it('appends the first N items after what the field holds', () => {
		const item = (n: number) => ({ id: `i${n}`, filename: `cell-${n}.png`, media_type: 'image', url: `/u/${n}` });
		const next = referencePatch([{ path: 'old' }], [item(1), item(2), item(3)], 2) as Record<string, unknown>[];
		expect(next.map((entry) => entry.path)).toEqual(['old', 'uploads/cell-1.png', 'uploads/cell-2.png']);
	});
});
