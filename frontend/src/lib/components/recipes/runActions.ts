import { api } from '$lib/services/api/index';
import type { SetupRun, SetupRunAction } from '$lib/services/api/setup';

/**
 * The two mutating calls `RecipeRunProgress` makes. The same run is reachable
 * through the first-run setup endpoints and through the admin recipes ones;
 * the surface rendering it decides which, so the component takes them rather
 * than picking one.
 */
export interface RecipeRunActions {
	applyAction(runId: string, action: SetupRunAction): Promise<SetupRun>;
	grantConsent(runId: string, stepKey: string): Promise<SetupRun>;
}

/** Drives `/api/setup/runs/...` — the first-run wizard. */
export const setupRunActions: RecipeRunActions = {
	applyAction: (runId, action) => api.applySetupRunAction(runId, action),
	grantConsent: (runId, stepKey) => api.grantSetupRunConsent(runId, stepKey)
};

/** Drives `/api/recipes/runs/...` — Admin → Recipes. */
export const adminRecipeRunActions: RecipeRunActions = {
	applyAction: (runId, action) => api.applyRecipeRunAction(runId, action),
	grantConsent: (runId, stepKey) => api.grantRecipeRunConsent(runId, stepKey)
};
