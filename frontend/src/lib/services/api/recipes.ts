import type { AxiosInstance } from 'axios';
import type { RecipeModeRow } from './recipeModes';
import type {
	ReadinessReport,
	RecipePresetLink,
	RecipeRunMode,
	SetupConsentGpuProfile,
	SetupConsentSlot,
	SetupRun,
	SetupRunAction
} from './setup';

export type { RecipePresetLink, RecipeRunMode };

export interface RecipeSlotVariants extends SetupConsentSlot {
	recipe_id: string;
	recipe_name: string;
	suggested_variant_id: string;
	suggested_reason: string;
	current_variant_id?: string;
}

export interface PresetSlotVariantsResponse {
	gpu: SetupConsentGpuProfile;
	slots: RecipeSlotVariants[];
}

export interface ModelSlotVariantsResponse {
	gpu: SetupConsentGpuProfile;
	slot: RecipeSlotVariants | null;
}

export interface SlotVariantDownloadRequest {
	recipe_id: string;
	artifact_id: string;
	variant_id: string;
}

export type RecipeSource = 'marketplace' | 'local' | 'plugin' | 'derived';

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
	/** When set, a run of this recipe has already completed. */
	last_completed_at: string | null;
	source: RecipeSource;
	/** The plugin that ships this recipe, or null for a core one. */
	plugin_id: string | null;
	step_count: number;
	presets: RecipePresetLink[];
	preset_id?: string | null;
	modes_total?: number | null;
	modes_ready?: number | null;
	missing_bytes?: number | null;
	installed_bytes?: number | null;
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
	/** Requires an accepted licence / access token before the source will serve it. */
	gated: boolean;
	license_url: string | null;
	source?: string | null;
	repo_id?: string | null;
}

export interface RecipeSmoke {
	preset_id: string;
	mode: string;
}

/** `GET /api/recipes/{id}` — the summary plus everything the recipe declares. */
export interface RecipeDetail extends RecipeSummary {
	steps: RecipeStepView[];
	artifacts: RecipeArtifact[];
	smoke: RecipeSmoke | null;
	/** Problems the loader hit reading this recipe, rendered as-is. */
	load_errors: string[];
	modes?: RecipeModeRow[];
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
		async createRecipeRun(recipeId: string): Promise<RecipeRun> {
			const response = await client.post(`/api/recipes/${recipeId}/runs`, {});
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

		async grantRecipeRunConsent(
			runId: string,
			stepKey: string,
			selections?: Record<string, string>,
			extra?: { modes?: string[]; extras?: string[]; target?: string }
		): Promise<RecipeRun> {
			const response = await client.post(`/api/recipes/runs/${runId}/consent/${stepKey}`, {
				selections: selections ?? {},
				...(extra ?? {})
			});
			return response.data;
		},

		async getPresetSlotVariants(presetId: string, modelType?: string): Promise<PresetSlotVariantsResponse> {
			const response = await client.get(`/api/recipes/variants/preset/${encodeURIComponent(presetId)}`, {
				params: modelType ? { model_type: modelType } : undefined
			});
			return response.data;
		},

		async getModelSlotVariants(modelId: string): Promise<ModelSlotVariantsResponse> {
			const response = await client.get(`/api/recipes/variants/model/${encodeURIComponent(modelId)}`);
			return response.data;
		},

		async downloadSlotVariant(
			payload: SlotVariantDownloadRequest
		): Promise<{ download_id: string; filename: string }> {
			const response = await client.post('/api/recipes/variants/download', payload);
			return response.data;
		},

		/** Which step kinds this instance can execute, and who contributes each. */
		async listRecipeStepKinds(): Promise<{ kinds: RecipeStepKind[] }> {
			const response = await client.get('/api/recipes/step-kinds');
			return response.data;
		}
	};
}
