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

export function withoutCoveredModeRecipes(recipes: readonly RecipeSummary[]): RecipeSummary[] {
	const covered = new Map<string, number>();
	for (const recipe of recipes) {
		if (recipe.source === 'derived' || !isModesRecipe(recipe)) continue;
		for (const link of recipe.presets ?? []) {
			covered.set(link.id, Math.max(covered.get(link.id) ?? 0, recipe.modes_total ?? 0));
		}
	}
	return recipes.filter(
		(recipe) =>
			recipe.source !== 'derived' ||
			!recipe.preset_id ||
			(covered.get(recipe.preset_id) ?? 0) < (recipe.modes_total ?? 0)
	);
}
