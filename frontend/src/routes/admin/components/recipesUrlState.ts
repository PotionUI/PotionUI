const RECIPE_PARAM = 'recipe';

export interface RecipesUrlState {
	recipeId: string | null;
}

/** Read the selected recipe out of the Recipes tab's own query param. The id
 * is returned as-is — whether it names a recipe this instance actually has is
 * the caller's check, once the catalog has loaded. */
export function readRecipesUrlState(searchParams: URLSearchParams): RecipesUrlState {
	return { recipeId: searchParams.get(RECIPE_PARAM) };
}

/** Write `state` onto `url`'s query params, returning a new URL. Nothing
 * selected removes the param rather than writing an empty one, so an idle
 * Recipes tab keeps a clean URL. */
export function writeRecipesUrlState(url: URL, state: RecipesUrlState): URL {
	const next = new URL(url);
	if (state.recipeId) {
		next.searchParams.set(RECIPE_PARAM, state.recipeId);
	} else {
		next.searchParams.delete(RECIPE_PARAM);
	}
	return next;
}
