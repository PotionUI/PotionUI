import type { RecipeSummary } from '$lib/services/api/recipes';

/** Where an "Install models" affordance sends an admin, and what it says. */
export interface InstallModelsTarget {
	href: string;
	label: string;
}

const LABEL = 'Install models';
const TAB_HREF = '/admin?tab=recipes';

function deepLink(recipeId: string): InstallModelsTarget {
	return { href: `${TAB_HREF}&recipe=${encodeURIComponent(recipeId)}`, label: LABEL };
}

/**
 * Resolve the Admin -> Recipes destination for a dead end that a recipe could
 * repair (an empty preset list, an empty model list).
 *
 * Returns `null` whenever there is nothing better to offer than today's
 * behaviour — a non-admin, or no recipe catalog to send them into — so callers
 * keep their existing /setup link in exactly those cases.
 *
 * `presetId` is the preset the caller is stuck on, when it has one. A recipe
 * that declares it is the obvious destination; several recipes declaring it (or
 * none) fall back to the catalog. With no preset in scope, a single-recipe
 * instance still deep-links, since there is only one thing an admin could mean.
 */
export function resolveInstallModelsTarget(
	recipes: RecipeSummary[] | null,
	presetId: string | null,
	isAdmin: boolean
): InstallModelsTarget | null {
	if (!isAdmin || !recipes || recipes.length === 0) return null;

	if (presetId) {
		const matches = recipes.filter((recipe) => recipe.preset_ids?.includes(presetId));
		if (matches.length === 1) return deepLink(matches[0].id);
		if (matches.length > 1) return { href: TAB_HREF, label: LABEL };
	}

	if (recipes.length === 1) return deepLink(recipes[0].id);
	return { href: TAB_HREF, label: LABEL };
}
