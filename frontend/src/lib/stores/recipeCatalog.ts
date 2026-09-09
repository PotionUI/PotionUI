import { writable, get } from 'svelte/store';
import { api } from '$lib/services/api/index';
import type { RecipeSummary } from '$lib/services/api/recipes';

/**
 * The recipe catalog, fetched once per page load and shared by every surface
 * that needs to answer "is there a recipe that installs this preset's models".
 *
 * `null` means "not known" — never fetched, or the fetch failed (the endpoint
 * is admin-only, so a regular user always lands here). Callers treat that as
 * "offer nothing extra" rather than "there are no recipes".
 */
export const recipeCatalog = writable<RecipeSummary[] | null>(null);

let inFlight: Promise<RecipeSummary[] | null> | null = null;

/**
 * Load the catalog if it isn't loaded yet. Concurrent callers share one
 * request; a failure resolves to `null` and is not cached, so a later caller
 * (e.g. after the user is granted admin) tries again.
 */
export async function loadRecipeCatalog(): Promise<RecipeSummary[] | null> {
	const cached = get(recipeCatalog);
	if (cached) return cached;
	if (inFlight) return inFlight;

	inFlight = api
		.listRecipes()
		.then((result) => {
			const recipes = result.recipes ?? [];
			recipeCatalog.set(recipes);
			return recipes;
		})
		.catch(() => null)
		.finally(() => {
			inFlight = null;
		});

	return inFlight;
}
