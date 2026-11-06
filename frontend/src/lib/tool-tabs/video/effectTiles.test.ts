import { describe, expect, it } from 'vitest';
import type { EffectTool } from '$lib/effects/catalog';
import { effectTiles, thumbRenderable, tileById, tileMatches } from './effectTiles';

function effect(id: string, extra: Partial<EffectTool> = {}, group = 'colour'): EffectTool {
	return {
		id,
		label: id,
		icon: 'editor-effects',
		description: '',
		params: [],
		core: true,
		block: { group, time: 'none', shader: { url: `/s/${id}`, etag: 'e' }, uniforms: [], textures: [], derive: {}, gain: null, live: null, analysis: false, analysisKeys: null },
		...extra
	} as EffectTool;
}

const lookEffect = effect(
	'look',
	{
		params: [
			{ name: 'look', type: 'look', default: '', options: [{ value: '', label: 'None', env: {} }, { value: 'reel', label: 'Reel', group: 'film', revision: '1', env: { a: 1 } }, { value: 'noir', label: 'Noir', group: 'mono', revision: '1', env: { a: 1 } }] as never },
			{ name: 'strength', type: 'float', default: 0.6, min: 0, max: 1 }
		]
	},
	'colour'
);
const grain = effect('film_grain', { params: [{ name: 'amount', type: 'float', default: 30 }] }, 'texture');
const fix = effect('fix_colors', { params: [] }, 'colour');
const analysed = effect('auto', {}, 'colour');
analysed.block.analysis = true;

describe('effect tiles', () => {
	it('makes one tile per look option, skipping the empty one, with the look filled in', () => {
		const tiles = effectTiles([lookEffect]);
		expect(tiles.map((tile) => [tile.id, tile.label, tile.group])).toEqual([
			['look:reel', 'Reel', 'Film'],
			['look:noir', 'Noir', 'Mono']
		]);
		expect(tiles[0].values).toEqual({ look: 'reel', strength: 0.6 });
		expect(tiles[0].lookParam).toBe('look');
	});

	it('puts the looks first, then one tile per other effect under its group', () => {
		const tiles = effectTiles([grain, fix, lookEffect]);
		expect(tiles.map((tile) => tile.id)).toEqual(['look:reel', 'look:noir', 'fix_colors', 'film_grain']);
		expect(tiles.at(-1)?.group).toBe('Texture');
		expect(tiles.at(-1)?.values).toEqual({ amount: 30 });
	});

	it('flags the tiles whose thumbnails can be drawn', () => {
		expect(thumbRenderable(grain)).toBe(true);
		expect(thumbRenderable(analysed)).toBe(false);
		expect(thumbRenderable(effect('x', { block: { ...grain.block, shader: null } } as never))).toBe(false);
		const withImage = effect('tex');
		withImage.block.textures = [{ uniform: 'u_t', from: 'p', kind: 'image2d' }];
		expect(thumbRenderable(withImage)).toBe(false);
	});

	it('matches a look tile by effect and look value, a plain tile by effect', () => {
		const [reel, noir] = effectTiles([lookEffect]);
		expect(tileMatches(reel, 'look', { look: 'reel' })).toBe(true);
		expect(tileMatches(noir, 'look', { look: 'reel' })).toBe(false);
		expect(tileMatches(reel, 'film_grain', { look: 'reel' })).toBe(false);
		const [grainTile] = effectTiles([grain]);
		expect(tileMatches(grainTile, 'film_grain', { amount: 1 })).toBe(true);
		expect(tileMatches(grainTile, null, null)).toBe(false);
	});

	it('finds a tile by id', () => {
		const tiles = effectTiles([grain]);
		expect(tileById(tiles, 'film_grain')?.label).toBe('film_grain');
		expect(tileById(tiles, 'nope')).toBeNull();
		expect(tileById(tiles, null)).toBeNull();
	});
});
