import type { RecipeSummary } from '$lib/services/api/recipes';
import { formatBytesShort } from '$lib/utils/format';

export function isModesRecipe(recipe: Pick<RecipeSummary, 'modes_total'>): boolean {
	return recipe.modes_total != null && recipe.modes_total > 0;
}

export function modesBadge(recipe: Pick<RecipeSummary, 'modes_total' | 'modes_ready'>): {
	label: string;
	variant: 'success' | 'warning';
} {
	const total = recipe.modes_total ?? 0;
	const ready = recipe.modes_ready ?? 0;
	if (total > 0 && ready >= total) {
		return { label: total === 1 ? 'Mode ready' : `All ${total} modes ready`, variant: 'success' };
	}
	return { label: `${ready} of ${total} modes ready`, variant: 'warning' };
}

export function modesToAdd(recipe: Pick<RecipeSummary, 'modes_total' | 'modes_ready'>): number {
	return Math.max(0, (recipe.modes_total ?? 0) - (recipe.modes_ready ?? 0));
}

export function modesDiskLine(
	recipe: Pick<RecipeSummary, 'modes_total' | 'modes_ready' | 'installed_bytes'>
): string {
	const parts: string[] = [];
	if (recipe.installed_bytes != null) parts.push(`${formatBytesShort(recipe.installed_bytes)} on disk`);
	const toAdd = modesToAdd(recipe);
	if (toAdd > 0) parts.push(`${toAdd} to add`);
	return parts.join(' · ');
}

export function partitionRecipes(recipes: readonly RecipeSummary[]): {
	modes: RecipeSummary[];
	starters: RecipeSummary[];
} {
	const modes: RecipeSummary[] = [];
	const starters: RecipeSummary[] = [];
	for (const recipe of recipes) (isModesRecipe(recipe) ? modes : starters).push(recipe);
	return { modes, starters };
}
