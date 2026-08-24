/**
 * Builds the request for a model search/browse fetch: preset-scoped
 * (`api.getPresetModels`) when a preset is active, global (`api.getModels`)
 * otherwise. Pulled out of ModelBrowserPanel.svelte so the preset-vs-global
 * branching and query-param shaping (join tag ids, drop empty strings) is
 * covered without mounting a component.
 */

export interface ModelSearchInput {
	modelType: string;
	presetId?: string | null;
	searchQuery?: string;
	limit: number;
	/** Ids of the user's own tag filter chips - AND semantics server-side. */
	tagIds?: string[];
	/** Admin-set `configuration.filter_tags` ids - OR semantics server-side,
	 *  independent of `tagIds`. */
	anyTagIds?: string[];
	favoritesOnly?: boolean;
}

export type ModelSearchRequest =
	| {
			kind: 'preset';
			presetId: string;
			modelType: string;
			search?: string;
			opts: { limit: number; tagIds?: string; anyTagIds?: string; favoritesOnly?: boolean };
	  }
	| {
			kind: 'global';
			params: {
				model_type: string;
				search?: string;
				include_tags: true;
				limit: number;
				all_models: true;
				favorites_only?: boolean;
				tag_ids?: string;
			};
	  };

function joinIds(ids: string[] | undefined): string | undefined {
	return ids && ids.length > 0 ? ids.join(',') : undefined;
}

export function buildModelSearchRequest(input: ModelSearchInput): ModelSearchRequest {
	const search = input.searchQuery?.trim() || undefined;
	const tagIds = joinIds(input.tagIds);
	const anyTagIds = joinIds(input.anyTagIds);
	const favoritesOnly = input.favoritesOnly || undefined;

	if (input.presetId) {
		return {
			kind: 'preset',
			presetId: input.presetId,
			modelType: input.modelType,
			search,
			opts: { limit: input.limit, tagIds, anyTagIds, favoritesOnly }
		};
	}

	return {
		kind: 'global',
		params: {
			model_type: input.modelType,
			search,
			include_tags: true,
			limit: input.limit,
			// Same access semantics as the preset branch, which asks the policy
			// with all_models=true: an admin picking a model is unrestricted,
			// everyone else still sees exactly their assigned models (the policy
			// ignores the flag for non-admins). Without it a bare-mounted picker
			// scopes even an admin to explicitly assigned models, so a model
			// nobody assigned - anything hand-dropped into the depot - reads as
			// "No models found" while the same field inside a preset form lists it.
			all_models: true,
			favorites_only: favoritesOnly,
			tag_ids: tagIds
		}
	};
}
