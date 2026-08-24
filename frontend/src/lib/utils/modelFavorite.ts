import { logger, getErrorMessage } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';

/**
 * Optimistic favorite toggle: applies the flip immediately via `apply`,
 * calls the API, and reverts through `apply` again on failure. Every
 * favorite-star caller (ModelField, LoraPickerField, ModelCollectionBrowser,
 * ModelCard) previously duplicated this same apply/await/revert shape with
 * its own local list(s) to patch - `apply` is how each caller's own state
 * (a card's local flag, a fetched list, a selected-item snapshot) plugs in.
 */
export async function toggleModelFavoriteOptimistic(
	model: { id: string; is_favorite?: boolean },
	apply: (favorite: boolean) => void,
	logContext = '[modelFavorite]'
): Promise<void> {
	const next = !model.is_favorite;
	apply(next);
	try {
		const response = await api.setModelFavorite(model.id, next);
		if (!response.success) throw new Error('Favorite update failed');
	} catch (error) {
		apply(!next);
		logger.error(`${logContext} Failed to toggle model favorite:`, getErrorMessage(error));
	}
}
