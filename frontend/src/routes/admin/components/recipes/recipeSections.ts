import { oneOf, type LibrarySectionMeta } from '$lib/components/library/librarySection';
import type { RecipeSummary } from '$lib/services/api/recipes';

export const RECIPE_CATEGORY_VALUES = ['image', 'video', 'audio', '3d', 'utility'] as const;
export type RecipeCategory = (typeof RECIPE_CATEGORY_VALUES)[number];
export type RecipeSection = RecipeCategory | 'all';

const CATEGORY_LABELS: Record<RecipeCategory, string> = {
	image: 'Image',
	video: 'Video',
	audio: 'Audio',
	'3d': '3D',
	utility: 'Utility'
};

const CATEGORY_ICONS: Record<RecipeCategory, string> = {
	image: 'photo',
	video: 'film',
	audio: 'audio',
	'3d': 'cube',
	utility: 'wand'
};

export const RECIPE_SECTIONS: readonly LibrarySectionMeta<RecipeSection>[] = [
	{ id: 'all', label: 'All recipes', icon: 'list-checks' },
	...RECIPE_CATEGORY_VALUES.map((category) => ({
		id: category,
		label: CATEGORY_LABELS[category],
		icon: CATEGORY_ICONS[category]
	}))
];

export function recipeCategoryLabel(category: string): string {
	return CATEGORY_LABELS[category as RecipeCategory] ?? category;
}

export function recipeCategoryIcon(category: string): string {
	return CATEGORY_ICONS[category as RecipeCategory] ?? 'list-checks';
}

export function recipeSectionFromParam(value: string | null): RecipeSection {
	return oneOf(value, RECIPE_SECTIONS.map((entry) => entry.id), 'all');
}

export function recipesInSection(recipes: readonly RecipeSummary[], section: RecipeSection): RecipeSummary[] {
	if (section === 'all') return [...recipes];
	return recipes.filter((recipe) => recipe.category === section);
}

export function recipeSectionCounts(recipes: readonly RecipeSummary[]): Partial<Record<RecipeSection, number>> {
	const counts: Partial<Record<RecipeSection, number>> = { all: recipes.length };
	for (const recipe of recipes) {
		const key = recipe.category as RecipeSection;
		counts[key] = (counts[key] ?? 0) + 1;
	}
	return counts;
}
