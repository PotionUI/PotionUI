import { api } from '$lib/services/api/index';
import type { APIResponse } from '$lib/types/api';
import type { User } from '$lib/stores/auth';
import type { AdminToolsetEntry, LLMConfig } from '$lib/types/llm';
import type { GenerationHistoryItem } from '$lib/types/history';

/**
 * Admin-specific API methods
 * These methods are only available to admin users
 */

// Admin API - Settings
export async function getSettings(): Promise<APIResponse<Record<string, unknown>>> {
	const response = await api.getClient().get('/api/settings');
	return response.data;
}

export async function updateSettings(settings: Record<string, unknown>): Promise<APIResponse> {
	const response = await api.getClient().put('/api/settings', settings);
	return response.data;
}

// Admin API - Semantic search / media indexing model status
export interface ActiveModelDownload {
	id: string;
	status: 'pending' | 'downloading' | 'paused';
	progress: number;
	downloaded_bytes: number;
	total_bytes: number | null;
	speed_bytes_per_sec: number | null;
}

export interface LocalModelFetchStatus {
	present: boolean;
	path: string;
	size: number | null;
	/** Resident in memory right now - distinct from `present` (on-disk). A
	 * lifecycle-managed model can be on disk and evicted, so `present` alone
	 * cannot answer "will the next request need to reload it first". */
	loaded: boolean;
	active_download: ActiveModelDownload | null;
}

export async function getPromptEmbeddingStatus(
	modelName?: string
): Promise<APIResponse<LocalModelFetchStatus>> {
	const response = await api
		.getClient()
		.get('/api/prompts/embedding-status', { params: modelName ? { model_name: modelName } : undefined });
	return response.data;
}

export async function getMediaModelsStatus(options?: {
	taggerModel?: string;
	visionModel?: string;
}): Promise<APIResponse<{ tagger: LocalModelFetchStatus; vision: LocalModelFetchStatus }>> {
	const params: Record<string, string> = {};
	if (options?.taggerModel) params.tagger_model = options.taggerModel;
	if (options?.visionModel) params.vision_model = options.visionModel;
	const response = await api.getClient().get('/api/media-index/models-status', { params });
	return response.data;
}

// Admin API - Users
export async function getUsers(): Promise<APIResponse<User[]>> {
	const response = await api.getClient().get('/api/users');
	return response.data;
}

export async function createUser(userData: {
	username: string;
	email: string;
	password: string;
	account_type: string;
}): Promise<APIResponse> {
	const response = await api.getClient().post('/api/users', userData);
	return response.data;
}

export async function updateUser(
	userId: string,
	userData: Partial<{
		username: string;
		email: string;
		password: string;
		account_type: string;
	}>
): Promise<APIResponse> {
	const response = await api.getClient().put(`/api/users/${userId}`, userData);
	return response.data;
}

export async function deleteUser(userId: string): Promise<APIResponse> {
	const response = await api.getClient().delete(`/api/users/${userId}`);
	return response.data;
}

// Admin API - LLM Assignments
export async function getUserLLMAssignments(
	userId: string
): Promise<APIResponse<{ user_id: string; llm_configs: LLMConfig[] }>> {
	const response = await api.getClient().get(`/api/llm/user-assignments/${userId}`);
	return response.data;
}

export async function assignLLMToUser(userId: string, llmConfigId: string): Promise<APIResponse> {
	const response = await api.getClient().post(`/api/llm/user-assignments`, {
		user_id: userId,
		llm_config_id: llmConfigId
	});
	return response.data;
}

export async function unassignLLMFromUser(
	userId: string,
	llmConfigId: string
): Promise<APIResponse> {
	const response = await api.getClient().delete(
		`/api/llm/user-assignments/${userId}/${llmConfigId}`
	);
	return response.data;
}

export async function getLLMConfigAssignments(
	llmConfigId: string
): Promise<APIResponse<{ llm_config_id: string; assignments: { user_id: string }[] }>> {
	const response = await api.getClient().get(`/api/llm/configurations/${llmConfigId}/assignments`);
	return response.data;
}

export async function getLLMAssignmentSummary(): Promise<APIResponse<AssignmentSummary>> {
	const response = await api.getClient().get('/api/llm/assignment-summary');
	return response.data;
}

// Admin API - Preset Assignments
export async function getPresetAssignments(
	presetId: string
): Promise<APIResponse<{ preset_db_id: string; assignments: PresetAssignment[] }>> {
	const response = await api.getClient().get(`/api/presets/${presetId}/assignments`);
	return response.data;
}

export async function assignPresetToUsers(
	presetId: string,
	userIds: string[]
): Promise<APIResponse> {
	const response = await api.getClient().post(`/api/presets/${presetId}/assign`, {
		user_ids: userIds
	});
	return response.data;
}

export async function unassignPresetFromUser(
	presetId: string,
	userId: string
): Promise<APIResponse> {
	const response = await api.getClient().post(
		`/api/presets/${presetId}/unassign/${userId}`
	);
	return response.data;
}

// Admin API - Model Assignment Summary
export interface AssignmentSummaryEntry {
	assignment_count: number;
	group_count: number;
}

export type AssignmentSummary = Record<string, AssignmentSummaryEntry>;

export interface ModelUserAssignment {
	user_id: string;
	model_id: string;
	assigned_at?: string;
}

export async function getModelAssignments(
	modelId: string
): Promise<APIResponse<{ model_id: string; assignments: ModelUserAssignment[] }>> {
	const response = await api.getClient().get(`/api/models/${modelId}/assignments`);
	return response.data;
}

export async function getModelAssignmentSummary(): Promise<APIResponse<AssignmentSummary>> {
	const response = await api.getClient().get('/api/models/assignment-summary');
	return response.data;
}

// Admin API - Presets Management
export async function installPreset(presetId: string): Promise<APIResponse> {
	const response = await api.getClient().post(`/api/presets/${presetId}/install`);
	return response.data;
}

export async function uninstallPreset(presetId: string): Promise<APIResponse> {
	const response = await api.getClient().post(`/api/presets/${presetId}/uninstall`);
	return response.data;
}

// Admin API - LLM Configurations
export async function createLLMConfiguration(configData: {
	name: string;
	type: string;
	model: string;
	api_key?: string;
	base_url?: string;
	enabled: boolean;
}): Promise<APIResponse> {
	const response = await api.getClient().post('/api/llm/configurations', configData);
	return response.data;
}

export async function updateLLMConfiguration(
	configId: string,
	configData: Partial<{
		name: string;
		type: string;
		model: string;
		api_key?: string;
		base_url?: string;
		enabled: boolean;
	}>
): Promise<APIResponse> {
	const response = await api.getClient().put(
		`/api/llm/configurations/${configId}`,
		configData
	);
	return response.data;
}

export async function deleteLLMConfiguration(configId: string): Promise<APIResponse> {
	const response = await api.getClient().delete(`/api/llm/configurations/${configId}`);
	return response.data;
}

// Admin API - User Groups
export async function getUserGroups(): Promise<APIResponse<UserGroup[]>> {
	const response = await api.getClient().get('/api/user-groups');
	return response.data;
}

export async function createUserGroup(data: {
	name: string;
	description?: string;
}): Promise<APIResponse> {
	const response = await api.getClient().post('/api/user-groups', data);
	return response.data;
}

export async function updateUserGroup(
	groupId: string,
	data: { name?: string; description?: string }
): Promise<APIResponse> {
	const response = await api.getClient().put(`/api/user-groups/${groupId}`, data);
	return response.data;
}

export async function deleteUserGroup(groupId: string): Promise<APIResponse> {
	const response = await api.getClient().delete(`/api/user-groups/${groupId}`);
	return response.data;
}

export async function getGroupMembers(groupId: string): Promise<APIResponse<UserGroupMember[]>> {
	const response = await api.getClient().get(`/api/user-groups/${groupId}/members`);
	return response.data;
}

export async function addUsersToGroup(
	groupId: string,
	userIds: string[]
): Promise<APIResponse> {
	const response = await api.getClient().post(`/api/user-groups/${groupId}/members`, {
		user_ids: userIds
	});
	return response.data;
}

export async function removeUserFromGroup(
	groupId: string,
	userId: string
): Promise<APIResponse> {
	const response = await api.getClient().delete(
		`/api/user-groups/${groupId}/members/${userId}`
	);
	return response.data;
}

export async function getGroupPresets(groupId: string): Promise<APIResponse<GroupPreset[]>> {
	const response = await api.getClient().get(`/api/user-groups/${groupId}/presets`);
	return response.data;
}

export async function assignPresetsToGroup(
	groupId: string,
	presetIds: string[]
): Promise<APIResponse> {
	const response = await api.getClient().post(`/api/user-groups/${groupId}/presets`, {
		preset_ids: presetIds
	});
	return response.data;
}

export async function unassignPresetFromGroup(
	groupId: string,
	presetId: string
): Promise<APIResponse> {
	const response = await api.getClient().delete(
		`/api/user-groups/${groupId}/presets/${presetId}`
	);
	return response.data;
}

export async function getGroupLLMs(groupId: string): Promise<APIResponse<GroupLLM[]>> {
	const response = await api.getClient().get(`/api/user-groups/${groupId}/llms`);
	return response.data;
}

export async function assignLLMsToGroup(
	groupId: string,
	llmConfigIds: string[]
): Promise<APIResponse> {
	const response = await api.getClient().post(`/api/user-groups/${groupId}/llms`, {
		llm_config_ids: llmConfigIds
	});
	return response.data;
}

export async function unassignLLMFromGroup(
	groupId: string,
	llmConfigId: string
): Promise<APIResponse> {
	const response = await api.getClient().delete(
		`/api/user-groups/${groupId}/llms/${llmConfigId}`
	);
	return response.data;
}

// Admin API - User-Model Assignments
export async function getUserModelAssignments(
	userId: string
): Promise<APIResponse<{ user_id: string; assignments: ModelAssignment[] }>> {
	const response = await api.getClient().get(`/api/models/user-assignments/${userId}`);
	return response.data;
}

export async function assignModelToUser(userId: string, modelId: string): Promise<APIResponse> {
	const response = await api.getClient().post(`/api/models/user-assignments`, {
		user_id: userId,
		model_id: modelId
	});
	return response.data;
}

export async function unassignModelFromUser(
	userId: string,
	modelId: string
): Promise<APIResponse> {
	const response = await api.getClient().delete(
		`/api/models/user-assignments/${userId}/${modelId}`
	);
	return response.data;
}

// Admin API - Group-Model Assignments
export async function getGroupModels(groupId: string): Promise<APIResponse<GroupModel[]>> {
	const response = await api.getClient().get(`/api/user-groups/${groupId}/models`);
	return response.data;
}

export async function assignModelsToGroup(
	groupId: string,
	modelIds: string[]
): Promise<APIResponse> {
	const response = await api.getClient().post(`/api/user-groups/${groupId}/models`, {
		model_ids: modelIds
	});
	return response.data;
}

export async function unassignModelFromGroup(
	groupId: string,
	modelId: string
): Promise<APIResponse> {
	const response = await api.getClient().delete(
		`/api/user-groups/${groupId}/models/${modelId}`
	);
	return response.data;
}

// Admin API - Dictionaries
export async function getModelsDictionary(): Promise<APIResponse<{ models: string[] }>> {
	const response = await api.getClient().get('/api/dictionaries/models');
	return response.data;
}

// ========== User/Group Assignment Types ==========

export interface UserGroup {
	id: string;
	name: string;
	description?: string;
	created_at?: string;
	updated_at?: string;
	member_count?: number;
	preset_count?: number;
	llm_count?: number;
	model_count?: number;
	/** True for the built-in ALL_USERS/ALL_ADMINS groups — hide/disable delete for these. */
	is_system?: boolean;
}

export interface UserGroupMember {
	id: string;
	group_id: string;
	user_id: string;
	assigned_at?: string;
	updated_at?: string;
}

export interface PresetAssignment {
	user_id: string;
	/** Installed `presets.id` foreign key; compare with `PresetInfo.preset_db_id`. */
	preset_id: string;
	assigned_at?: string;
}

export interface ModelAssignment {
	model_id: string;
	model_name?: string;
	model_type?: string;
	assigned_at?: string;
}

export interface GroupPreset {
	id: string;
	group_id: string;
	/** Installed `presets.id` foreign key; mutations still accept the public preset ID. */
	preset_id: string;
	assigned_at?: string;
	updated_at?: string;
}

export interface GroupLLM {
	id: string;
	group_id: string;
	llm_config_id: string;
	assigned_at?: string;
	updated_at?: string;
}

export interface GroupModel {
	id: string;
	group_id: string;
	model_id: string;
	assigned_at?: string;
	updated_at?: string;
}

// ========== Backend API ==========

/** One configuration field an engine declares for itself. */
export interface EngineField {
	name: string;
	label: string;
	type: 'string' | 'number' | 'boolean';
	required: boolean;
	default: string | number | boolean | null;
	description: string | null;
	/** Render as a password input. */
	secret: boolean;
	/** When present, render a <select> over these choices instead of a free input. */
	options: (string | number)[] | null;
}

/**
 * An engine as described by the server. Engines are an open set — `native` is
 * built in, others come from plugins — so nothing in the frontend may hardcode
 * an engine name or assume which fields it needs.
 */
export interface EngineDescriptor {
	engine: string;
	label: string;
	/** Exactly one backend, auto-provisioned; never offered in the create form. */
	singleton: boolean;
	fields: EngineField[];
}

/**
 * An admin-only operation a backend declares on itself (e.g. the native
 * engine's "Clear VRAM" / "Restart Backend"). The frontend never hardcodes
 * what any engine can do - it only renders what `Backend.quick_actions`
 * returns and POSTs to `endpoint`. `poll_health_after` means the action is
 * expected to interrupt the connection (e.g. a restart); the caller should
 * poll GET /health until the app responds again before refreshing state.
 */
export interface BackendQuickAction {
	id: string;
	label: string;
	icon: string;
	endpoint: string;
	method: string;
	confirm?: string;
	danger?: boolean;
	poll_health_after?: boolean;
}

export interface Backend {
	id: string;
	name: string;
	engine: string;
	enabled: boolean;
	is_default: boolean;
	priority: number;
	timeout_seconds: number;
	quick_actions?: BackendQuickAction[];
	/** Engine-specific settings, flat. Which keys exist depends on the engine. */
	[key: string]: unknown;
}

export interface BackendHealth {
	backend_id: string;
	backend_name: string;
	backend_engine: string;
	enabled: boolean;
	health: {
		status: string;
		error?: string;
		message?: string;
	};
}

// List all backends
export async function getBackends(): Promise<APIResponse<Backend[]>> {
	const response = await api.getClient().get('/api/backends');
	return response.data;
}

// Get enabled backends
export async function getEnabledBackends(): Promise<APIResponse<Backend[]>> {
	const response = await api.getClient().get('/api/backends/enabled');
	return response.data;
}

// Get default backend for an engine
export async function getDefaultBackend(engine: string): Promise<APIResponse<Backend>> {
	const response = await api.getClient().get('/api/backends/default', { params: { engine } });
	return response.data;
}

// Set a backend as the default for its engine
export async function setDefaultBackend(backendId: string): Promise<APIResponse<Backend>> {
	const response = await api.getClient().post(`/api/backends/${backendId}/set-default`);
	return response.data;
}

// Get specific backend
export async function getBackend(backendId: string): Promise<APIResponse<Backend>> {
	const response = await api.getClient().get(`/api/backends/${backendId}`);
	return response.data;
}

// Create backend
export async function createBackend(backendData: Partial<Backend>): Promise<APIResponse<Backend>> {
	const response = await api.getClient().post('/api/backends', backendData);
	return response.data;
}

// Update backend
export async function updateBackend(backendId: string, backendData: Partial<Backend>): Promise<APIResponse<Backend>> {
	const response = await api.getClient().put(`/api/backends/${backendId}`, backendData);
	return response.data;
}

// Delete backend
export async function deleteBackend(backendId: string): Promise<APIResponse> {
	const response = await api.getClient().delete(`/api/backends/${backendId}`);
	return response.data;
}

// Test backend connection
export async function testBackend(backendId: string): Promise<APIResponse> {
	const response = await api.getClient().post(`/api/backends/${backendId}/test`);
	return response.data;
}

// Get backend health
export async function getBackendHealth(backendId: string): Promise<APIResponse> {
	const response = await api.getClient().get(`/api/backends/${backendId}/health`);
	return response.data;
}

// Get backend system info
export async function getBackendSystemInfo(backendId: string): Promise<APIResponse> {
	const response = await api.getClient().get(`/api/backends/${backendId}/system-info`);
	return response.data;
}

// Get all backends health
export async function getAllBackendsHealth(): Promise<APIResponse<BackendHealth[]>> {
	const response = await api.getClient().get('/api/backends/health');
	return response.data;
}

// Get supported backend engines (built-in + plugin-provided)
export async function getBackendEngines(): Promise<APIResponse<EngineDescriptor[]>> {
	const response = await api.getClient().get('/api/backends/engines');
	return response.data;
}

/** A filename that exists with different byte sizes on different backends (e.g. a quantised copy). */
export interface ModelSizeConflict {
	model_type: string;
	filename: string;
	known_size: number;
	reported_size: number;
	backend_id: string;
}

/** This backend's own copy hashes differently from the model's canonical digest - same
 *  identity, possibly even the same size, but different content. That row is excluded
 *  from routing until the file is re-synced and the backend re-indexed. */
export interface ModelDigestConflict {
	model_type: string;
	filename: string;
	known_digest: string;
	reported_digest: string;
	backend_id: string;
}

export interface IndexModelsResult {
	backend_id: string;
	listed: number;
	created: number;
	matched: number;
	removed: number;
	size_conflicts: ModelSizeConflict[];
	digest_conflicts: ModelDigestConflict[];
	ambiguous: string[];
}

// Index the models a backend can see (walk disk for native, query the server for ComfyUI, etc.)
export async function indexBackendModels(
	backendId: string
): Promise<APIResponse<IndexModelsResult>> {
	const response = await api.getClient().post(`/api/backends/${backendId}/index-models`);
	return response.data;
}

/** Per-backend model stats - from the last `indexBackendModels` run, not a live re-scan. */
export interface BackendStats {
	backend_id: string;
	indexed_models: number;
	total_size_bytes: number;
	total_size_gb: number;
	last_indexed_at: string | null;
}

export async function getBackendStats(backendId: string): Promise<APIResponse<BackendStats>> {
	const response = await api.getClient().get(`/api/backends/${backendId}/stats`);
	return response.data;
}

// ========== Backend Optimizations ==========

/** One requirement an optimization needs before it can be installed. */
export interface OptimizationRequirement {
	id: string;
	label: string;
	met: boolean;
	detail: string | null;
}

/** Server-reported hardware/toolchain state used to gate optimizations. */
export interface SystemProbeReport {
	cuda_available: boolean;
	compute_capability: [number, number] | null;
	gpu_name: string | null;
	gpu_vram_gb: number | null;
	torch_version: string;
	torch_cuda_version: string | null;
	nvcc_found: boolean;
	/** [major, minor], not a formatted string. */
	nvcc_version: [number, number] | null;
	nvcc_cuda_matches_torch: boolean;
	nvcc_source?: 'system' | 'venv' | null;
	gcc_found: boolean;
	python_h_found: boolean;
	sageattention_version: string | null;
	triton_version: string | null;
	flash_attn_version: string | null;
	xformers_version: string | null;
	active_backend: string;
	available_backends: string[];
}

/** Current install/activation state for one catalog optimization. */
export interface OptimizationStatus {
	opt_id: string;
	name: string;
	description: string;
	benefit: string;
	needs_restart: boolean;
	installed: boolean;
	installed_version: string | null;
	active: boolean;
	installable: boolean;
	requirements: OptimizationRequirement[];
}

/** Effective on/off state of the admin-toggleable native engine flags. */
export interface EngineFlags {
	torch_compile: boolean;
	stream_prefetch: boolean;
}

export interface BackendOptimizations {
	system: SystemProbeReport;
	optimizations: OptimizationStatus[];
	pinned_backend: string | null;
	engine_flags: EngineFlags;
}

export interface OptimizationJobStatus {
	active: boolean;
	status?: 'running' | 'success' | 'failed' | 'cancelled';
	opt_id?: string;
	/** New lines since the requested offset, not the full log. */
	log?: string[];
	next_offset?: number;
	result?: { active_backend: string } | null;
	error?: string | null;
}

// Get system probe + optimization catalog status for a native backend
export async function getBackendOptimizations(
	backendId: string
): Promise<APIResponse<BackendOptimizations>> {
	const response = await api.getClient().get(`/api/backends/${backendId}/optimizations`);
	return response.data;
}

// Kick off (or report unmet requirements/busy for) installing one optimization
export async function installBackendOptimization(
	backendId: string,
	optId: string
): Promise<APIResponse<{ opt_id: string; status: string }>> {
	const response = await api
		.getClient()
		.post(`/api/backends/${backendId}/optimizations/${optId}/install`);
	return response.data;
}

// Poll the currently running (or last finished) install job, from a log offset
export async function getCurrentOptimizationJob(
	backendId: string,
	offset: number
): Promise<APIResponse<OptimizationJobStatus>> {
	const response = await api
		.getClient()
		.get(`/api/backends/${backendId}/optimizations/jobs/current`, { params: { offset } });
	return response.data;
}

// Cancel the currently running install job
export async function cancelCurrentOptimizationJob(
	backendId: string
): Promise<APIResponse<{ cancelled: boolean }>> {
	const response = await api
		.getClient()
		.post(`/api/backends/${backendId}/optimizations/jobs/current/cancel`);
	return response.data;
}

// Pin (or clear, via "auto") the attention backend used on the native engine
export async function setAttentionBackend(
	backendId: string,
	backend: 'auto' | 'sdpa' | 'sage' | 'sage2' | 'flash'
): Promise<APIResponse<{ pinned_backend: string | null; active_backend: string }>> {
	const response = await api
		.getClient()
		.put(`/api/backends/${backendId}/optimizations/attention-backend`, { backend });
	return response.data;
}

// Toggle native engine flags (torch.compile / stream prefetch); applied live, no restart
export async function setEngineFlags(
	backendId: string,
	flags: { torch_compile?: 'on' | 'off'; stream_prefetch?: 'on' | 'off' }
): Promise<APIResponse<{ engine_flags: EngineFlags }>> {
	const response = await api
		.getClient()
		.put(`/api/backends/${backendId}/optimizations/engine-flags`, flags);
	return response.data;
}

// Restart the API process in place (os.execv) — used after installs that need_restart
export async function restartApp(): Promise<APIResponse> {
	const response = await api.getClient().post('/api/admin/restart');
	return response.data;
}

// Invoke one of a backend's self-described quick actions (Backend.quick_actions).
// Generic on purpose: the frontend doesn't know what any given action does,
// only that it's a method/endpoint pair the backend told it about.
export async function invokeBackendQuickAction(action: BackendQuickAction): Promise<APIResponse> {
	const method = action.method.toUpperCase();
	const client = api.getClient();
	const response =
		method === 'GET'
			? await client.get(action.endpoint)
			: method === 'PUT'
				? await client.put(action.endpoint)
				: method === 'DELETE'
					? await client.delete(action.endpoint)
					: await client.post(action.endpoint);
	return response.data;
}

/** One attention backend's measured result in a benchmark run. */
export interface AttentionBenchmarkResult {
	backend: string;
	ms: number | null;
	speedup: number | null;
	ok: boolean;
	error: string | null;
}

/** A synchronous attention-backend timing sweep for one native backend. */
export interface AttentionBenchmark {
	dtype: string;
	shape: number[];
	iterations: number;
	active_backend: string;
	results: AttentionBenchmarkResult[];
}

// Run a synchronous attention-backend timing sweep (~5-15s)
export async function runOptimizationBenchmark(
	backendId: string
): Promise<APIResponse<AttentionBenchmark>> {
	const response = await api
		.getClient()
		.post(`/api/backends/${backendId}/optimizations/benchmark`);
	return response.data;
}

// ========== Chat Session Debug Viewer ==========

/** One row of the admin session list (`GET /api/chat/admin/sessions`). */
export interface AdminChatSessionSummary {
	id: string;
	user_id: string;
	mode: string;
	name: string | null;
	status: string;
	llm_config_id: string | null;
	created_at: string;
	updated_at: string;
	username: string;
	email: string;
	message_count: number;
}

export interface AdminChatSessionsResult {
	sessions: AdminChatSessionSummary[];
	total: number;
	limit: number;
	offset: number;
	/** Whether the `chat_llm_call_tracing` setting is currently enabled. */
	tracing_enabled: boolean;
}

/** One tool call recorded on an assistant message's `metadata.tool_executions`. */
export interface AdminChatToolExecution {
	tool_name: string;
	arguments: unknown;
	result: {
		success: boolean;
		data: unknown;
		error: string | null;
	};
	duration_ms: number;
	pending_approval: boolean;
}

/** One timed step in a `behavior_trace` manifest's `steps[]`. */
export interface AdminChatBehaviorStep {
	step: string;
	duration_ms: number;
}

/**
 * The per-message behavior-trace manifest persisted on
 * `metadata.behavior_trace` (see `_build_behavior_trace` in
 * `src/features/chat/conversation.py`). Present for assistant messages sent
 * after the tracing feature landed; older messages won't have it.
 */
export interface AdminChatBehaviorTrace {
	version: number;
	mode: string | null;
	system_prompt_source: string;
	resources: { uri: string; type: string }[];
	memory: { note_ids?: string[]; by_scope?: Record<string, unknown> } | Record<string, unknown>;
	pre_chat_actions: string[];
	tools_used: string[];
	token_counts: { prompt: number | null; completion: number | null };
	steps: AdminChatBehaviorStep[];
	history?: { messages_sent: number; messages_total: number; truncated: boolean };
}

/** Rich metadata attached to an assistant message (`message.metadata`). */
export interface AdminChatMessageMetadata {
	model?: string;
	tokens_used?: number | null;
	prompt_tokens?: number | null;
	completion_tokens?: number | null;
	tool_executions?: AdminChatToolExecution[];
	behavior_trace?: AdminChatBehaviorTrace;
	[key: string]: unknown;
}

/** A message within a session, as returned by the admin detail endpoint. */
export interface AdminChatMessage {
	id: string;
	session_id: string;
	role: 'user' | 'assistant';
	content: string;
	parsed_content: unknown;
	created_at: string;
	tokens_used: number | null;
	prompt_tokens: number | null;
	completion_tokens: number | null;
	tool_executions: unknown;
	metadata: AdminChatMessageMetadata | null;
}

export interface AdminChatSessionDetail extends AdminChatSessionSummary {
	original_text: string | null;
	title_generated: boolean;
	closed_at: string | null;
	metadata: unknown;
	messages: AdminChatMessage[];
}

/** One request message in the exact array sent to the LLM provider. */
export interface AdminChatTraceMessage {
	role: string;
	content: unknown;
	tool_calls?: unknown;
	tool_call_id?: string;
	name?: string;
}

/** One LLM call made during a chat turn (`chat_call_traces` row). */
export interface AdminChatCallTrace {
	id: string;
	session_id: string;
	user_id: string | null;
	/** Groups this trace onto a message; null = not yet attributed (e.g. title generation). */
	message_id: string | null;
	purpose: 'chat' | 'chat_tools' | 'title' | (string & {});
	iteration: number;
	provider: string;
	model: string;
	request_system: string | null;
	request_messages: AdminChatTraceMessage[];
	request_params: Record<string, unknown>;
	request_tools: string[] | null;
	response_text: string | null;
	response_tool_calls: unknown[] | null;
	prompt_tokens: number | null;
	completion_tokens: number | null;
	duration_ms: number;
	created_at: string;
}

export interface AdminChatSessionDetailResult {
	session: AdminChatSessionDetail;
	traces: AdminChatCallTrace[];
}

export async function getAdminChatSessions(
	search?: string,
	limit = 20,
	offset = 0
): Promise<APIResponse<AdminChatSessionsResult>> {
	const response = await api.getClient().get('/api/chat/admin/sessions', {
		params: { search: search || undefined, limit, offset }
	});
	return response.data;
}

export async function getAdminChatSessionDetail(
	sessionId: string
): Promise<APIResponse<AdminChatSessionDetailResult>> {
	const response = await api.getClient().get(`/api/chat/admin/sessions/${sessionId}`);
	return response.data;
}

export async function clearChatCallTraces(
	sessionId?: string
): Promise<APIResponse<{ deleted: number }>> {
	const response = await api.getClient().delete('/api/chat/admin/traces', {
		params: sessionId ? { session_id: sessionId } : undefined
	});
	return response.data;
}

// ========== Global Generation History (admin) ==========

/** One row of the admin generations list (`GET /api/admin/generations`) - the
 *  same shape `getGenerationHistory` returns, plus the owner and whether a
 *  persisted run report exists to drill into. */
export interface AdminGenerationListItem extends GenerationHistoryItem {
	user_id: string;
	has_run_report: boolean;
}

export interface AdminGenerationsResult {
	generations: AdminGenerationListItem[];
	total: number;
}

/** One status update recorded during the run (`run_report.status_history[]`). */
export interface RunReportStatusEntry {
	at: string;
	pipe_id: string | number | null;
	step: string;
	message: string | null;
	progress: number;
}

/** Wall-clock bounds for one pipe's execution (`run_report.pipe_timers`). */
export interface RunReportPipeTimer {
	started_at: string | null;
	ended_at: string | null;
}

/** One artifact emitted during the run (`run_report.artifacts[]`). */
export interface RunReportArtifact {
	at: string;
	pipe_id: string | number | null;
	artifact_type: string;
	artifact_data: Record<string, unknown>;
}

/** Latest message recorded for a plugin `generation.output` message type
 *  (`run_report.plugin_outputs`) - unlike the live `pluginOutputs` on
 *  `GenerationState`, this carries no `asset`, so the admin viewer can't
 *  resolve the plugin's live renderer component and falls back to a generic
 *  rendering. */
export interface RunReportPluginOutput {
	plugin_id: string;
	message: unknown;
	at: string;
}

/** The persisted run report for one generation (`run_report_repository`),
 *  mirroring what the user-facing history drawer showed live during the run. */
export interface RunReport {
	schema_version: number;
	status_history: RunReportStatusEntry[];
	pipe_timers: Record<string, RunReportPipeTimer>;
	artifacts: RunReportArtifact[];
	plugin_outputs: Record<string, RunReportPluginOutput>;
	prompt_template: { positive: string; negative: string } | null;
}

export interface AdminGenerationDetailResult {
	generation: AdminGenerationListItem;
	/** Null for generations that predate run-report persistence. */
	run_report: RunReport | null;
}

export interface AdminGenerationsParams {
	limit?: number;
	offset?: number;
	status?: string;
	userId?: string;
	search?: string;
	createdFrom?: string;
	createdTo?: string;
}

export async function getAdminGenerations(
	params: AdminGenerationsParams = {}
): Promise<APIResponse<AdminGenerationsResult>> {
	const response = await api.getClient().get('/api/admin/generations', {
		params: {
			limit: params.limit,
			offset: params.offset,
			status: params.status || undefined,
			user_id: params.userId || undefined,
			search: params.search || undefined,
			created_from: params.createdFrom || undefined,
			created_to: params.createdTo || undefined
		}
	});
	return response.data;
}

export async function getAdminGenerationDetail(
	generationId: string
): Promise<APIResponse<AdminGenerationDetailResult>> {
	const response = await api.getClient().get(`/api/admin/generations/${generationId}`);
	return response.data;
}

// Admin API - MCP per-user access (global on/off is the `mcp_enabled` SYSTEM
// setting, via getSettings/updateSettings above)

export interface McpUserSetting {
	user_id: string;
	enabled: boolean;
}

export async function getMcpUserSetting(userId: string): Promise<APIResponse<McpUserSetting>> {
	const response = await api.getClient().get(`/api/mcp/admin/users/${userId}`);
	return response.data;
}

export async function setMcpUserSetting(
	userId: string,
	enabled: boolean
): Promise<APIResponse<McpUserSetting>> {
	const response = await api.getClient().put(`/api/mcp/admin/users/${userId}`, { enabled });
	return response.data;
}

// Admin API - Tool governance (per-LLM-config tool enable/lock, see
// /api/llm/configurations/{id}/toolset - the same tool can be enabled in one
// config and disabled in another)

export async function getAdminToolset(configId: string): Promise<APIResponse<AdminToolsetEntry[]>> {
	const response = await api.getClient().get(`/api/llm/configurations/${configId}/toolset`);
	return response.data;
}

export async function updateToolGovernance(
	configId: string,
	toolName: string,
	changes: { enabled?: boolean; locked?: boolean }
): Promise<APIResponse<{ name: string; enabled: boolean; locked: boolean }>> {
	const response = await api.getClient().put(`/api/llm/configurations/${configId}/toolset/${toolName}`, changes);
	return response.data;
}
