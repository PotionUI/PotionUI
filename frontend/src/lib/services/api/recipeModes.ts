import type { AxiosInstance } from 'axios';
import type { SetupConsentGpuProfile, SetupConsentProvider, SetupRun } from './setup';

export type ModeTargetKind = 'local' | 'remote' | 'external';

export interface ModeTargetOption {
	backend_id: string;
	label: string;
	kind: ModeTargetKind;
	free_bytes: number | null;
	gpu_name: string | null;
	vram_gb: number | null;
}

export interface ModeTarget extends ModeTargetOption {
	options: ModeTargetOption[];
}

export interface ModeConsentMode {
	id: string;
	label: string;
	short_description?: string | null;
	selected: boolean;
	locked: boolean;
	ready: boolean;
	slot_ids: string[];
	extra_slot_ids: string[];
	any_of: string[][];
	missing_bytes: number;
}

export interface ModeConsentVariant {
	id: string;
	label: string;
	precision: string | null;
	filename: string;
	size_bytes: number | null;
	installed: boolean;
	gated: boolean;
	license_url: string | null;
	source?: string | null;
	repo_id?: string | null;
	is_recipe_default?: boolean;
	fast?: boolean | null;
	recommended: boolean;
	best_fit: boolean;
	note: string | null;
}

export interface ModeConsentSlot {
	id: string;
	label: string;
	field?: string;
	kind?: string;
	model_type: string;
	required: boolean;
	extra: boolean;
	manual: boolean;
	used_by: string[];
	disk_id: string | null;
	satisfied_by?: string | null;
	recommended_variant_id: string;
	best_fit_variant_id: string | null;
	reason: string;
	variants: ModeConsentVariant[];
}

export interface ModeConsentDisk {
	id: string;
	target: string;
	label: string;
	free_bytes: number | null;
	reserve_bytes: number;
	model_types?: string[];
}

export interface ModeConsentEstimate {
	bytes_per_sec: number;
	basis: string;
}

export interface ModeConsentRequest {
	kind: 'modes';
	preset_id: string;
	preset_name: string;
	target: ModeTarget;
	gpu: (Partial<SetupConsentGpuProfile> & { name?: string | null; vram_gb?: number }) | null;
	modes: ModeConsentMode[];
	slots: ModeConsentSlot[];
	disk: ModeConsentDisk[];
	estimate: ModeConsentEstimate | null;
	providers: SetupConsentProvider[];
	warnings: string[];
}

export interface ModesGrantBody {
	selections?: Record<string, string>;
	modes?: string[];
	extras?: string[];
	target?: string;
}

export interface RecipeModeRow {
	id: string;
	label: string;
	short_description: string | null;
	ready: boolean;
	size_bytes: number | null;
	own_bytes: number | null;
	missing_bytes: number | null;
	removable: boolean;
	shares_with: string[];
}

export interface RecipePlanStep {
	key: string;
	kind: string;
	title: string;
	onboarding_only: boolean;
	preview: { summary?: string; error?: string } | null;
}

export interface RecipePlanResponse {
	recipe_id: string;
	steps: RecipePlanStep[];
	consent_request: ModeConsentRequest | null;
	totals: { download_bytes: number | null; file_count: number | null };
}

export interface ModeRemovalFile {
	model_id: string;
	label: string;
	filename: string;
	model_type?: string;
	size_bytes: number | null;
	reason_code?: string;
	reason?: string;
	modes?: string[];
}

export interface ModeRemovalPreview {
	preset_id: string;
	mode: string;
	label: string;
	target: string;
	delete: ModeRemovalFile[];
	keep: ModeRemovalFile[];
	frees_bytes: number;
	clears_defaults: { field: string }[];
}

export interface ModeRemovalResult {
	deleted: unknown[];
	kept: unknown[];
	freed_bytes: number;
}

export interface ModeDownloadFile {
	slot_id: string;
	display_name: string;
	filename: string;
	size_bytes: number | null;
	downloaded_bytes: number;
	status: 'pending' | 'downloading' | 'paused' | 'completed' | 'failed' | 'cancelled';
	download_id?: string | null;
}

export interface ModeDownloadProgress {
	files: ModeDownloadFile[];
	files_done: number;
	files_total: number;
	speed_bytes_per_sec: number | null;
	eta_seconds: number | null;
}

export function createRecipeModesApi(client: AxiosInstance) {
	return {
		async planRecipe(
			recipeId: string,
			body: { preselect_modes?: string[]; target?: string } = {}
		): Promise<RecipePlanResponse> {
			const response = await client.post(`/api/recipes/${encodeURIComponent(recipeId)}/plan`, body);
			return response.data;
		},

		async startRecipeModesRun(recipeId: string, preselectModes: string[] = []): Promise<SetupRun> {
			const response = await client.post(`/api/recipes/${encodeURIComponent(recipeId)}/runs`, {
				recipe_version: null,
				preselect_modes: preselectModes
			});
			return response.data;
		},

		async grantRecipeModesConsent(runId: string, stepKey: string, body: ModesGrantBody): Promise<SetupRun> {
			const response = await client.post(`/api/recipes/runs/${runId}/consent/${stepKey}`, body);
			return response.data;
		},

		async getModeRemovalPreview(recipeId: string, mode: string): Promise<ModeRemovalPreview> {
			const response = await client.get(
				`/api/recipes/${encodeURIComponent(recipeId)}/modes/${encodeURIComponent(mode)}/removal`
			);
			return response.data;
		},

		async removeModeFiles(recipeId: string, mode: string, modelIds: string[]): Promise<ModeRemovalResult> {
			const response = await client.post(
				`/api/recipes/${encodeURIComponent(recipeId)}/modes/${encodeURIComponent(mode)}/removal`,
				{ model_ids: modelIds }
			);
			return response.data;
		}
	};
}
