import type { AxiosInstance } from 'axios';

/** Public first-run status - `GET /api/setup/status` (unauthenticated). */
export interface SetupStatus {
	needs_owner: boolean;
	registration_open: boolean;
	claim_requires_token: boolean;
}

export type ReadinessArea = 'service' | 'execution' | 'content' | 'generation_proven';
export type ReadinessStatus = 'ready' | 'not_ready' | 'degraded';

export interface ReadinessCheck {
	area: ReadinessArea;
	status: ReadinessStatus;
	code: string;
	message: string;
	action: string | null;
}

export interface ReadinessReport {
	overall: ReadinessStatus;
	checks: ReadinessCheck[];
}

/** Lifecycle of a whole durable setup run. Non-terminal: pending/running/
 * awaiting_consent/paused. Terminal (immutable): completed/failed/cancelled. */
export type SetupRunStatus =
	| 'pending'
	| 'running'
	| 'awaiting_consent'
	| 'paused'
	| 'completed'
	| 'failed'
	| 'cancelled';

/** Status of a single step attempt within a run. */
export type SetupStepStatus =
	| 'running'
	| 'succeeded'
	| 'action_required'
	| 'awaiting_consent'
	| 'failed'
	| 'cancelled';

/** The action names the run-actions endpoint accepts. */
export type SetupRunAction = 'pause' | 'resume' | 'cancel' | 'retry_step';

export interface SetupStepAttempt {
	step_key: string;
	attempt: number;
	status: SetupStepStatus;
	progress_current: number | null;
	progress_total: number | null;
	progress_unit: string | null;
	safe_output: Record<string, unknown> | null;
	error_code: string | null;
	safe_error_detail: string | null;
	/** A step's repair hint (e.g. "Open Administration -> Backends"), split
	 * out of `safe_output` server-side so the UI never has to fish a magic
	 * key out of a free-form output dict. */
	safe_suggested_action: string | null;
	started_at: string | null;
	finished_at: string | null;
}

/** One entry of a run's recipe's ordered execution plan (`SetupRunStepView`
 * — src/features/setup/run_dto.py), whether or not it has been attempted
 * yet, so a not-yet-started step can render as "pending" instead of simply
 * being absent. */
export interface SetupRunStepView {
	step_key: string;
	title: string;
	kind: string;
	ordinal: number;
	/** "pending", or the latest attempt's status. */
	status: string;
	attempts: SetupStepAttempt[];
}

/** The durable setup run as the admin UI renders it (redacted server-side). */
export interface SetupRun {
	id: string;
	recipe_id: string;
	recipe_version: number;
	scope: string;
	status: SetupRunStatus;
	current_step: string | null;
	safe_input: Record<string, unknown> | null;
	safe_output: Record<string, unknown> | null;
	error_code: string | null;
	safe_error_detail: string | null;
	created_at: string | null;
	updated_at: string | null;
	completed_at: string | null;
	/** The recipe's ordered step manifest, each entry carrying whatever
	 * attempts exist for it. Empty when the run's recipe can no longer be
	 * resolved — fall back to `attempts` (flat, unordered) in that case. */
	steps: SetupRunStepView[];
	attempts: SetupStepAttempt[];
}

/** One entry of `GET /api/setup/recipes` — a startable recipe, before any
 * run exists for it. */
export interface SetupRecipe {
	id: string;
	name: string;
	summary: string;
	description: string;
	engine: string;
	category: string;
	artifact_count: number;
	total_download_bytes: number | null;
	preset_name: string | null;
	/** When set, a run of this recipe has already completed — the catalog
	 * renders it as "Installed" with a "Run again" action instead of "Start". */
	last_completed_at: string | null;
}

/** An artifact a paused-for-consent step wants to download, as surfaced in
 * that attempt's `safe_output.consent_request`. */
export interface SetupConsentArtifact {
	id: string;
	display_name: string;
	size_bytes: number | null;
	kind: string;
}

/** A provider that would serve one or more of the pending downloads and
 * takes a credential it doesn't currently have configured — see
 * `ArtifactsPlanExecutor._unconfigured_credential_providers`. `field_name` is
 * the settings key to write (e.g. `api_key`), reused verbatim against the
 * same `PUT /api/plugins/{id}/settings` endpoint Admin -> Plugins uses. */
export interface SetupConsentProvider {
	id: string;
	name: string;
	website: string;
	field_name: string;
	configured: boolean;
}

export interface SetupConsentRequest {
	artifacts: SetupConsentArtifact[];
	total_bytes: number | null;
	/** Present only when at least one pending download's provider takes a
	 * credential and doesn't have one configured yet. */
	providers?: SetupConsentProvider[];
}

export function createSetupApi(client: AxiosInstance) {
	return {
		async getSetupStatus(): Promise<SetupStatus> {
			const response = await client.get('/api/setup/status');
			return response.data;
		},

		async getReadiness(recipeId?: string): Promise<ReadinessReport> {
			const response = await client.get('/api/readiness', {
				params: recipeId ? { recipe_id: recipeId } : undefined
			});
			return response.data;
		},

		/** Start a guided setup run, or return the already-active one — the
		 * endpoint is idempotent under an active run (admin-only, 404-not-403). */
		async createSetupRun(
			recipeId: string,
			options?: { recipeVersion?: number; safeInput?: Record<string, unknown> }
		): Promise<SetupRun> {
			const response = await client.post('/api/setup/runs', {
				recipe_id: recipeId,
				recipe_version: options?.recipeVersion ?? 1,
				safe_input: options?.safeInput ?? {}
			});
			return response.data;
		},

		/** Durable setup-run detail, including redacted step attempts. */
		async getSetupRun(runId: string): Promise<SetupRun> {
			const response = await client.get(`/api/setup/runs/${runId}`);
			return response.data;
		},

		/** Read-only discovery for the /setup panel: the currently active run
		 * (if any), regardless of which browser/session started it. Never
		 * creates anything — throws a 404 when nothing is active. */
		async getSetupActiveRun(): Promise<SetupRun> {
			const response = await client.get('/api/setup/runs/active');
			return response.data;
		},

		/** The catalog of recipes a guided setup run can be started from. */
		async getSetupRecipes(): Promise<{ recipes: SetupRecipe[] }> {
			const response = await client.get('/api/setup/recipes');
			return response.data;
		},

		/** Apply pause|resume|cancel|retry_step. `retry_step` only succeeds on a
		 * failed run and reopens its current (failed) step for a fresh attempt. */
		async applySetupRunAction(runId: string, action: SetupRunAction): Promise<SetupRun> {
			const response = await client.post(`/api/setup/runs/${runId}/actions/${action}`);
			return response.data;
		},

		/** Approve the download(s) a paused step described in its
		 * `consent_request` and resume the run. Only valid while that step is
		 * awaiting consent — a stale/mismatched `stepKey` 409s with a plain
		 * message. */
		async grantSetupRunConsent(runId: string, stepKey: string): Promise<SetupRun> {
			const response = await client.post(`/api/setup/runs/${runId}/actions/grant_consent`, {
				step_key: stepKey
			});
			return response.data;
		},

		/** Save one credential-style setting (e.g. `api_key`) for a provider
		 * named in a `SetupConsentProvider` — the exact `PUT
		 * /api/plugins/{id}/settings` endpoint Admin -> Plugins already saves
		 * provider credentials through, reused here so the setup consent gate
		 * never invents a second path to the same data. Provider settings are
		 * stored under the plugin id `{providerId}-provider` (see
		 * `ProviderRegistry._get_provider_settings`). */
		async saveSetupProviderCredential(
			providerId: string,
			fieldName: string,
			value: string
		): Promise<void> {
			await client.put(`/api/plugins/${providerId}-provider/settings`, {
				settings: { [fieldName]: value }
			});
		}
	};
}
