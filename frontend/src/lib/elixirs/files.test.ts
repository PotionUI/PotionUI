import { describe, expect, it } from 'vitest';
import { drawnCount, drawnKeys, pickLine } from './files';
import type { MediaAsset } from './types';

const assets: MediaAsset[] = ['a', 'b', 'c', 'd'].map((item_id) => ({ kind: 'library', item_id, media_type: 'image' }));

describe('files pick', () => {
	it('uses every file unless the step draws at random', () => {
		expect(drawnCount({ assets })).toBe(4);
		expect(pickLine({ assets })).toBeNull();
		expect(pickLine({ assets, pick: 'all', pick_count: 2 })).toBeNull();
	});

	it('names the random pick and never draws more than it holds', () => {
		expect(pickLine({ assets, pick: 'random', pick_count: 3 })).toBe('Random 3 of 4');
		expect(drawnCount({ assets, pick: 'random', pick_count: 40 })).toBe(4);
		expect(drawnCount({ assets, pick: 'random' })).toBe(1);
		expect(pickLine({ assets: [], pick: 'random', pick_count: 3 })).toBeNull();
	});

	it('knows which files the current brew drew', () => {
		const run = {
			status: 'running',
			node_states: { photos: { status: 'done', draw: { seed: 7, count: 2, of: 4 }, manifest: { images: [assets[1], assets[3]] } } }
		};
		const everything = { status: 'running', node_states: { photos: { status: 'done', manifest: { images: assets } } } };

		expect([...(drawnKeys(run, 'photos') ?? [])]).toEqual(['library:b', 'library:d']);
		expect(drawnKeys(everything, 'photos')).toBeNull();
		expect(drawnKeys(null, 'photos')).toBeNull();
	});
});
