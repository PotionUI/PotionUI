import type { AxiosInstance } from 'axios';
import type { ReadinessReport, RecipeRunMode, SetupRun, SetupRunAction } from './setup';

export type { RecipeRunMode };

/** Where a recipe was scanned from — the same three roots presets use. */
export type RecipeSource = 'marketplace' | 'local' | 'plugin';

/** One entry of `GET /api/recipes`. */
export interface RecipeSummary {
	id: string;
	name: string;
	summary: string;
	description: string;
	engine: string;
	category: string;
	artifact_count: number;
	total_download_bytes: number | null;
	preset_name: string | null;
	/** When set, a run of this recipe has already completed. */
	last_completed_at: string | null;
	source: RecipeSource;
	/** The plugin that ships this recipe, or null for a core one. */
	plugin_id: string | null;
	step_count: number;
	/** Every preset this recipe installs — the mapping the "Install models"
	 * entry points resolve a preset against. */
	preset_ids: string[];
}

export interface RecipeStepView {
	key: string;
	kind: string;
	title: string;
	/** A step the wizard runs on first-run only; an admin-started run skips it. */
	onboarding_only: boolean;
}

export interface RecipeArtifact {
	id: string;
	kind: string;
	model_type: string;
	filename: string;
	display_name: string;
	size_bytes: number | null;
	required: boolean;
}

export interface RecipePresetRef {
	preset_id: string;
	path_hint: string;
}

export interface RecipeSmoke {
	preset_id: string;
	mode: string;
}

/** `GET /api/recipes/{id}` — the summary plus everything the recipe declares. */
export interface RecipeDetail extends RecipeSummary {
	steps: RecipeStepView[];
	artifacts: RecipeArtifact[];
	presets: RecipePresetRef[];
	smoke: RecipeSmoke | null;
	/** Problems the loader hit reading this recipe, rendered as-is. */
	load_errors: string[];
}

/** A durable recipe run. The wizard and this tab render the same server DTO,
 * so this is `SetupRun` under the name the admin surface calls it — kept as a
 * name rather than collapsed, because a caller reading `RecipeRun` should not
 * have to know the wizard owns the shape. */
export type RecipeRun = SetupRun;

export interface RecipeStepKind {
	kind: string;
	source: 'core' | 'plugin';
	plugin_id: string | null;
}

export function createRecipesApi(client: AxiosInstance) {
	return {
		/** The full recipe catalog. Admin-only: a regular user gets a 403 here,
		 * unlike the wizard's setup routes, which answer 404-not-403. */
		async listRecipes(): Promise<{ recipes: RecipeSummary[] }> {
			const response = await client.get('/api/recipes');
			return response.data;
		},

		async getRecipe(recipeId: string): Promise<RecipeDetail> {
			const response = await client.get(`/api/recipes/${recipeId}`);
			return response.data;
		},

		/** Readiness scoped to one recipe — the same report `/api/readiness`
		 * returns, answering "could this recipe run right now". */
		async getRecipeReadiness(recipeId: string): Promise<ReadinessReport> {
			const response = await client.get(`/api/recipes/${recipeId}/readiness`);
			return response.data;
		},

		/** Start a run. 409s when another run is already active. */
		async createRecipeRun(recipeId: string, recipeVersion: number | null = null): Promise<RecipeRun> {
			const response = await client.post(`/api/recipes/${recipeId}/runs`, {
				recipe_version: recipeVersion
			});
			return response.data;
		},

		/** Run history, newest first. The server bounds `limit` to 1-200 and 422s
		 * outside that. */
		async listRecipeRuns(options?: { recipeId?: string; limit?: number }): Promise<{ runs: RecipeRun[] }> {
			const params: Record<string, string | number> = {};
			if (options?.recipeId) params.recipe_id = options.recipeId;
			if (options?.limit != null) params.limit = options.limit;
			const response = await client.get('/api/recipes/runs', {
				params: Object.keys(params).length ? params : undefined
			});
			return response.data;
		},

		async getRecipeRun(runId: string): Promise<RecipeRun> {
			const response = await client.get(`/api/recipes/runs/${runId}`);
			return response.data;
		},

		async applyRecipeRunAction(runId: string, action: SetupRunAction): Promise<RecipeRun> {
			const response = await client.post(`/api/recipes/runs/${runId}/actions`, { action });
			return response.data;
		},

		async grantRecipeRunConsent(runId: string, stepKey: string): Promise<RecipeRun> {
			const response = await client.post(`/api/recipes/runs/${runId}/consent/${stepKey}`);
			return response.data;
		},

		/** Which step kinds this instance can execute, and who contributes each. */
		async listRecipeStepKinds(): Promise<{ kinds: RecipeStepKind[] }> {
			const response = await client.get('/api/recipes/step-kinds');
			return response.data;
		}
	};
}
