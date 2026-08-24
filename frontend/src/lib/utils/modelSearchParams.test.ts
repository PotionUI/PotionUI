import { describe, expect, it } from 'vitest';
import { buildModelSearchRequest } from './modelSearchParams';

describe('buildModelSearchRequest', () => {
	it('builds a global request when no preset is active', () => {
		const request = buildModelSearchRequest({ modelType: 'lora', limit: 100 });
		expect(request).toEqual({
			kind: 'global',
			params: {
				model_type: 'lora',
				search: undefined,
				include_tags: true,
				limit: 100,
				all_models: true,
				favorites_only: undefined,
				tag_ids: undefined
			}
		});
	});

	it('asks for unrestricted access on the global branch, matching the preset branch', () => {
		// The preset branch resolves access with all_models=true server-side. A
		// bare mount that omits the flag scopes even an admin to explicitly
		// assigned models, so a hand-dropped depot file lists in a preset form
		// but reads as "No models found" in a standalone picker.
		const request = buildModelSearchRequest({ modelType: 'detection_segm', limit: 10 });
		expect(request.kind).toBe('global');
		expect(request.kind === 'global' && request.params.all_models).toBe(true);
	});

	it('builds a preset-scoped request when a preset is active', () => {
		const request = buildModelSearchRequest({
			modelType: 'checkpoint',
			presetId: 'sdxl-anime',
			limit: 50
		});
		expect(request).toEqual({
			kind: 'preset',
			presetId: 'sdxl-anime',
			modelType: 'checkpoint',
			search: undefined,
			opts: { limit: 50, tagIds: undefined, anyTagIds: undefined, favoritesOnly: undefined }
		});
	});

	it('trims the search query and drops it when empty', () => {
		expect(buildModelSearchRequest({ modelType: 'lora', limit: 10, searchQuery: '  neon  ' })).toMatchObject({
			params: { search: 'neon' }
		});
		expect(buildModelSearchRequest({ modelType: 'lora', limit: 10, searchQuery: '   ' })).toMatchObject({
			params: { search: undefined }
		});
	});

	it('joins tagIds for both request kinds', () => {
		const global = buildModelSearchRequest({ modelType: 'lora', limit: 10, tagIds: ['a', 'b'] });
		expect(global).toMatchObject({ params: { tag_ids: 'a,b' } });

		const preset = buildModelSearchRequest({
			modelType: 'lora',
			presetId: 'p1',
			limit: 10,
			tagIds: ['a', 'b']
		});
		expect(preset).toMatchObject({ opts: { tagIds: 'a,b' } });
	});

	it('only applies anyTagIds (admin filter_tags) to preset-scoped requests', () => {
		const global = buildModelSearchRequest({
			modelType: 'lora',
			limit: 10,
			anyTagIds: ['sdxl']
		});
		expect(global.kind).toBe('global');
		expect((global as any).params.any_tag_ids).toBeUndefined();

		const preset = buildModelSearchRequest({
			modelType: 'lora',
			presetId: 'p1',
			limit: 10,
			anyTagIds: ['sdxl']
		});
		expect(preset).toMatchObject({ opts: { anyTagIds: 'sdxl' } });
	});

	it('omits favoritesOnly/favorites_only when false', () => {
		const global = buildModelSearchRequest({ modelType: 'lora', limit: 10, favoritesOnly: false });
		expect(global).toMatchObject({ params: { favorites_only: undefined } });

		const preset = buildModelSearchRequest({
			modelType: 'lora',
			presetId: 'p1',
			limit: 10,
			favoritesOnly: true
		});
		expect(preset).toMatchObject({ opts: { favoritesOnly: true } });
	});
});
