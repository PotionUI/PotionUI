// Core types
import type { PromptRelayValue } from './tabs';
import type { VideoDirectorValue } from './videoDirector';
import type { MusicDirectorValue } from './musicDirector';
import type { Segment } from './segments';
import type { VariablesMap } from '$lib/utils/variableDefs';

export interface APIResponse<T = unknown> {
	success: boolean;
	data?: T;
	message?: string;
	error?: string;
}

export interface PromptPair {
	positive: string;
	negative: string;
}

// ── Structured prompt segments (sent with each generation) ──────────────────
// Field names MUST match the backend contract exactly.
export interface SegmentPhrasebookInput {
	phrasebook_value_id: string;
	category_path: string;
	value: string;
}

export interface SegmentInput {
	channel: 'positive' | 'negative';
	prompt_index: number; // default 0; multi-prompt tab index
	segment_index: number; // order within channel
	segment_type: 'content' | 'break';
	text: string; // chip-resolved plain text
	is_disabled: boolean;
	name?: string | null;
	color?: string | null;
	description?: string | null;
	phrasebooks: SegmentPhrasebookInput[];
}

// Inbound shape from getHistory / getGenerationById (may be absent on old rows).
export interface GenerationSegment extends SegmentInput {
	id: string;
	generation_id: string;
}

export interface GenerationRequest {
	preset_id: string;
	prompt?: string; // Legacy, deprecated - use prompts array
	negative_prompt?: string; // Legacy, deprecated - use prompts array
	prompts?: PromptPair[]; // Array of prompt pairs
	mode?: string;
	/** Selects the mode's preset variant's form (see PresetModeVariant). Omitted for
	 *  presets/modes with a single implicit variant. */
	form_name?: string;
	form_data?: Record<string, unknown>;
	tag_ids?: string[];
	collection_ids?: string[];
	prompt_state?: Record<string, unknown>;
	segments?: SegmentInput[];
	/** Prompt variables (name -> value template), bound at expansion time and
	 *  referenced from the prompt as `${name}` (see src/features/generation/dto.py
	 *  `GenerationRequest.variables` and src/features/prompt/expander.py). Sourced
	 *  from the submitting tab's `Tab.variables`. */
	variables?: Record<string, string>;
	/** Id of the tab that submitted this generation, used to scope queue state per-tab. */
	tab_id?: string;
	/** Library prompt (src/features/prompt_database) this generation's segments were
	 *  applied from, if any — feeds the "Used in generations" strip on the prompt's
	 *  detail page. Never implies the generation still matches the prompt verbatim;
	 *  it's a provenance link, not a live binding. */
	source_prompt_id?: string | null;
}

/** One selectable form variant within a preset mode (`GET /api/presets/{id}/modes`). */
export interface PresetModeVariant {
	name: string;
	label: string;
	description?: string;
	examples?: string[];
	default: boolean;
	order: number;
}

export interface PresetModeInfo {
	name: string;
	label: string;
	variants: PresetModeVariant[];
	/** The contributing plugin's id when this mode came from a plugin's
	 *  `preset_modes:` instead of the preset's own preset.yml. */
	source_plugin?: string | null;
}

/** Body of a 422 response from `POST /api/generations/start` when the submitted
 *  `form_data` fails preset-schema validation. The backend response shape is still
 *  settling — treat `field_errors`/`coercions`/`stripped` as possibly absent and
 *  narrow with `isFormValidationErrorResponse` (`$lib/utils/formValidationErrors`)
 *  rather than casting directly. */
export interface FormValidationErrorResponse {
	error: 'form_validation_failed';
	field_errors: Record<string, string[]>;
	coercions?: string[];
	stripped?: string[];
	message?: string;
}

/** The backend the Generation Router actually picked for this generation
 *  (`src/features/generation/orchestrator.py`'s `start_generation` return) -
 *  also re-sent on the first `generation_status` WebSocket message. */
export interface GenerationRoutingBackend {
	id: string;
	name: string;
	engine: string;
	/** One-line "why this backend" from the router, or `null` when the
	 *  router isn't wired. */
	routing_reason: string | null;
}

export interface StartGenerationResponseData {
	generation_id: string;
	status: GenerationStatus;
	/** 0-based position among ALL pending work, or null if it started running immediately. */
	queue_position: number | null;
	backend?: GenerationRoutingBackend;
}

export interface QueuedGenerationSummary {
	generation_id: string;
	backend_id?: string;
	tab_id?: string;
	enqueued_at?: string;
	queue_position: number | null;
}

export interface RunningGenerationSummary {
	generation_id: string;
	backend_id?: string;
	preset_id?: string;
	tab_id?: string;
	progress?: number;
}

export interface GenerationQueueSnapshot {
	pending: QueuedGenerationSummary[];
	running: RunningGenerationSummary[];
}

/** `POST /api/generations/memory-preview` response - a non-blocking,
 *  checkpoint-based estimate for a request that has NOT started. Never
 *  called a "lower bound" or "at least" anywhere: quantization/streaming can
 *  bring real residency below it, and a component the preset pins outside a
 *  form picker can push real usage above it. Never a fit verdict either way -
 *  read `coverage`/`uncertainty` alongside `device`/`budget` rather than the
 *  number alone. See docs/user/hardware-requirements.md. */
export interface MemoryPreviewResult {
	estimate: {
		/** GB, or `null` when no active model reference has a known size, or
		 *  when the active model set itself could not be resolved (see
		 *  `coverage.active_set_resolved`). */
		checkpoint_estimate_gb: number | null;
		weights_gb: number;
		activation_gb: number;
		margin: number;
		basis: string;
	};
	coverage: {
		known: Array<{ ref: string; size_gb: number }>;
		unknown: string[];
		/** `false` when the request itself couldn't be resolved (e.g. an
		 *  in-progress or invalid Video/Music Director document, or any other
		 *  pipeline-build failure) - render this as "the request could not be
		 *  resolved", never "no model references". */
		active_set_resolved: boolean;
		pinned_components_uncounted: boolean;
		uncertainty: string[];
	};
	device: {
		kind: 'local' | 'remote' | 'none' | 'unknown';
		free_gb: number | null;
		total_gb: number | null;
		provenance: string;
	};
	budget: {
		/** The backend-wide budget alone (its `gpu_max_vram`, bounded by
		 *  `device`'s free VRAM when there is one) - never a per-pipe hint
		 *  folded in. See `pipe_hints_gb` for those. */
		configured_gb: number | null;
		source: string;
		/** Each ACTIVE pipe's own per-stage `vram_limit_gb` hint (e.g. a tiled
		 *  detailer's own tile budget), composed against the same backend cap
		 *  individually - a preset can declare several with different values,
		 *  so no single entry speaks for the whole request. */
		pipe_hints_gb: Array<{ pipe: string; hint_gb: number; composed_gb: number | null }>;
	};
	backend: {
		id: string;
		name: string;
		engine: string;
		driver: string;
	};
}

export interface GenerationStatus {
	id: string;
	generation_id?: string;
	status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
	progress?: number;
	current_step?: string;
	total_steps?: number;
	current_step_num?: number;
	message?: string;
	created_at: string;
	started_at?: string;
	completed_at?: string;
	current_image?: string;
	pipe_id?: number;
	pipe_name?: string;
}

export interface PresetGalleryItem {
	src: string;
	caption?: string;
	prompt?: string;
	seed?: number;
	mode?: string;
}

export interface PresetMedia {
	cover?: string;
	/** Absent on the list endpoint (`GET /api/presets`); present on the detail endpoint. */
	gallery?: PresetGalleryItem[];
}

/** Optional author-supplied hardware guidance, shown at preset-choice time. All fields optional. */
export interface PresetRequirements {
	min_vram_gb?: number;
	recommended_vram_gb?: number;
	min_ram_gb?: number;
}

/** Last-evaluated counts from `GET /api/presets/{id}/requirements` - `null` until that
 *  endpoint has run at least once for this preset+backend. Never computed by the list/detail
 *  endpoints themselves. See docs/presets.md "Requirements". */
export interface PresetRequirementsSummary {
	ok: number;
	/** Hard misses only - a `missing` entry with `optional: true` is counted under
	 * `optional_missing` instead, never here. */
	missing: number;
	unknown: number;
	optional_missing: number;
}

export interface PresetInfo {
	id: string;
	name: string;
	version: string;
	description?: string;
	tags: string[];
	category?: string;
	source?: string;
	engine?: string;
	media?: PresetMedia;
	requires?: PresetRequirements;
	requirements_summary?: PresetRequirementsSummary | null;
	// Admin-only fields (present when `listPresets(includeUninstalled = true)` is called).
	installed?: boolean;
	/** Database relationship ID for the installed preset (admin list only). */
	preset_db_id?: string;
	assignment_count?: number;
	group_count?: number;
}

// ── Preset requirements (typed, live-checked entries - distinct from the static
//    `requires:` VRAM/RAM guidance above). See docs/presets.md "Requirements". ──
export type RequirementStatus = 'ok' | 'missing' | 'unknown';

export interface RequirementAction {
	kind: 'open_downloader' | 'open_backends' | 'open_url';
	payload: Record<string, unknown>;
}

export interface RequirementResultInfo {
	status: RequirementStatus;
	detail: string;
	hint?: string | null;
	action?: RequirementAction | null;
	/** The `requirements:` entry's own `type:` (`binary`, `python_package`, `model`,
	 *  `vram_min_gb`, `platform`, or an engine-specific type a plugin registered). */
	type?: string;
	/** The entry's most identifying string (a checker's `describe()`, or a fallback
	 *  field pulled from the raw entry) - what to show as this row's name. */
	name?: string;
	optional?: boolean;
	/** Present only on a backend-scoped entry (e.g. `comfyui_node`) - which
	 *  backend this result was evaluated against. */
	backend_id?: string;
}

/** One candidate backend of the preset's engine, with its own fully-scoped
 *  summary - lets the requirements tab offer a backend selector alongside
 *  the chosen backend's `results`/`summary`. */
export interface RequirementBackendInfo {
	id: string;
	name: string;
	is_default: boolean;
	summary: PresetRequirementsSummary;
}

export interface PresetRequirementsResponse {
	results: RequirementResultInfo[];
	summary: PresetRequirementsSummary;
	backends: RequirementBackendInfo[];
	checked_at: number;
}

// ── Per-preset configuration (admin-authored, distinct from the user-facing form) ──
export interface PresetConfigurationEntry {
	key: string;
	/** Only `model_tags` exists today; unrecognized types should be rendered as read-only/skipped. */
	type: 'model_tags' | (string & {});
	label: string;
	description?: string;
	value: number[] | string[] | null;
}

export interface PresetConfigurationResponse {
	preset_id: string;
	entries: PresetConfigurationEntry[];
}

// ── Per-mode form field overrides (admin-set default/editable/visible per field) ──
export interface PresetFormOverrideOption {
	label: string;
	value: unknown;
}

export interface PresetFormOverridePatch {
	default?: unknown;
	editable?: boolean;
	visible?: boolean;
}

export interface PresetFormOverrideField {
	name: string;
	label: string;
	/** Underlying field type (`string`, `number`, `boolean`, `select`, …) - drives which
	 *  editor the admin "default" override uses; unrecognized types fall back to text. */
	type: string;
	preset_default?: unknown;
	/** Present only when the inventory can enumerate choices (e.g. `select` fields). */
	options?: PresetFormOverrideOption[];
	override: PresetFormOverridePatch | null;
	/** Label of the preset-form tab this field belongs to, or `null` when the
	 *  field sits outside any tab. */
	tab: string | null;
}

export interface PresetFormOverridesResponse {
	preset_id: string;
	mode: string;
	modes: string[];
	fields: PresetFormOverrideField[];
	/** Tab labels in declaration order; empty for a flat form with no tabs. */
	tabs: string[];
}

export interface TagUsageRef {
	preset_id: string;
	preset_name: string;
	key: string;
}

// ── Model download recommendations (surfaced inside a model field's configuration) ──
export type ModelRecommendationSource =
	| { provider: string; ref: string; link?: never; sha256?: never }
	| { link: string; sha256?: string; provider?: never; ref?: never };

export type ModelRecommendation = {
	name: string;
	description?: string;
	size?: string;
	installed: boolean;
} & ModelRecommendationSource;

export interface ModelDownloadStatus {
	status: 'pending' | 'running' | 'completed' | 'failed';
	progress: number | null;
	error: string | null;
}

// Session Management Types
export interface PromptTabSessionData {
	prompt: string;
	negativePrompt: string;
	promptSegments: Segment[];
	negativePromptSegments: Segment[];
}

export interface SessionData {
	selectedPreset?: string;
	selectedMode?: string;
	/** Name of the mode's preset variant selected when this session slot was saved. */
	selectedVariant?: string;
	/** `PresetInfo.version` at save time — used to show a non-blocking "preset changed
	 *  since this session was saved" notice on restore. */
	presetVersion?: string;
	prompt?: string;
	negativePrompt?: string;
	promptSegments?: Segment[];
	negativePromptSegments?: Segment[];
	/** Legacy key names for promptSegments/negativePromptSegments, tolerated when
	 *  loading a session saved before the rename. Never written on save. */
	segments?: Segment[];
	negativeSegments?: Segment[];
	// Multi-prompt support
	promptTabs?: PromptTabSessionData[];
	activePromptTab?: number;
	// Prompt-relay timeline state
	promptRelay?: PromptRelayValue;
	// Video Director editor state
	videoDirector?: VideoDirectorValue;
	// Music Director editor state
	musicDirector?: MusicDirectorValue;
	formData?: Record<string, unknown>;
	/** Prompt variables saved with this session (see Tab.variables). */
	variables?: VariablesMap;
	seed?: number;
	// Panel layout for the generate page ('two' | 'three' panes)
	layoutMode?: 'two' | 'three';
	// Whether the generation settings form is folded to its compact rail
	leftPanelCollapsed?: boolean;
	// Whether the docked workbench pane is folded to its compact rail
	workbenchCollapsed?: boolean;
	// Width (px) of the prompts pane in three-pane layout mode
	promptPanelWidth?: number;
	// Horizontal folding state for the positive/negative segment editors
	positiveSegmentsCollapsed?: boolean;
	negativeSegmentsCollapsed?: boolean;
	/** Per-section fold state for `type: section` form fields, keyed by
	 *  `${preset}/${mode}/${fieldPath}` (see sectionState.ts). Absent until
	 *  the user folds/unfolds a section that has `children:`. */
	sectionCollapsed?: Record<string, boolean>;
	// Workbench preview pane height (px, as a string) and left panel width (px).
	workbenchMaxHeight?: string;
	leftPanelWidth?: number;
}

export interface ModeBasedSessionData {
	[mode: string]: SessionData;
}

export interface Session {
	id: string;
	preset_id: string;
	name: string;
	data: ModeBasedSessionData;
	created_at: string;
	updated_at: string;
}

export interface SaveSessionRequest {
	preset_id: string;
	name: string;
	data: ModeBasedSessionData;
}

export interface UpdateSessionRequest {
	name: string;
	data: ModeBasedSessionData;
}

// Session history ("Session history" surface) — read-only past saves of a
// session. Restoring one is client-side only: load its `data` into the tab
// like any session load, then a normal Save makes it the newest version.
export interface SessionVersionSummary {
	version_number: number;
	created_at: string;
	/** Jargon-free label for the save, e.g. the preset/mode name at save time. */
	summary: string;
}

export interface SessionVersionDetail extends SessionVersionSummary {
	data: ModeBasedSessionData;
}

// Phrasebook types
export type PhrasebookStateFilter = 'all' | 'active' | 'inactive';

export interface PhrasebookCategory {
	id: string;
	name: string;
	path: string;
	parent_id?: string;
	description: string;
	is_active: boolean;
	created_at: string;
	updated_at: string;
	user_id?: string;
}

export interface PhrasebookValue {
	id: string;
	category_id: string;
	label: string;
	value: string;
	sort_order: number;
	is_active: boolean;
	preview_file_id?: string;
	preview_generation_id?: string;
	created_at: string;
	updated_at: string;
	user_id?: string;
	category_path?: string;
	category_name?: string;
	category_is_active?: boolean;
}

export interface PhrasebookSearchResult {
	current_category: PhrasebookCategory | null;
	child_categories: PhrasebookCategory[];
	values: PhrasebookValue[];
	path: string;
	total_children: number;
	total_values: number;
}

export type PhrasebookFindMode = 'contains' | 'word' | 'regex';
export type PhrasebookFindScope = 'all' | 'values' | 'categories';
export type PhrasebookValueField = 'label' | 'value';

export interface PhrasebookMatchSpan {
	field: string;
	start: number;
	end: number;
}

export interface PhrasebookFindCategoryHit extends PhrasebookCategory {
	matches: PhrasebookMatchSpan[];
}

export interface PhrasebookFindValueHit extends PhrasebookValue {
	category_path: string;
	category_name: string;
	category_is_active: boolean;
	matches: PhrasebookMatchSpan[];
}

export interface PhrasebookFindParams {
	q: string;
	mode: PhrasebookFindMode;
	case_sensitive: boolean;
	scope: PhrasebookFindScope;
	include_inactive: boolean;
	path_prefix: string;
	fields: PhrasebookValueField[];
	limit?: number;
}

export interface PhrasebookFindResult {
	query: string;
	mode: PhrasebookFindMode;
	case_sensitive: boolean;
	scope: PhrasebookFindScope;
	categories: PhrasebookFindCategoryHit[];
	values: PhrasebookFindValueHit[];
	total_categories: number;
	total_values: number;
}

export interface PhrasebookBatchOp {
	id: string;
	label: string;
	component: string | null;
	has_preview: boolean;
	source: string;
}

export interface PhrasebookBatchRequest {
	op: string;
	value_ids: string[];
	params: Record<string, unknown>;
}

export interface PhrasebookBatchOutcome {
	updated: PhrasebookValue[];
	skipped: string[];
	deleted: string[];
	message: string;
}

export interface PhrasebookBatchPreviewItem {
	id: string;
	field: PhrasebookValueField;
	before: string;
	after: string;
}

export interface PhrasebookBatchPreview {
	items: PhrasebookBatchPreviewItem[];
	changed: number;
	unchanged: string[];
}

export interface PhrasebookReplaceParams {
	find: string;
	replace: string;
	mode: PhrasebookFindMode;
	case_sensitive: boolean;
	fields: PhrasebookValueField[];
}

export interface GeneratePreviewRequest {
	session_id: string;
	prompt_template: string;
	mode: string;
	negative_prompt?: string;
	seed?: number;
	value_ids?: string[];
}

export interface GeneratePreviewResult {
	total: number;
	started: number;
	failed: number;
	generations: Array<{
		value_id: string;
		value_label: string;
		generation_id: string;
		rendered_prompt: string;
	}>;
}

// Chat Session Types
export interface ChatMessageResponse {
	id: string;
	session_id: string;
	role: 'user' | 'assistant' | 'system';
	content: string;
	parsed_content?: {
		modifiedPrompt?: string;
		explanation?: string;
	} | null;
	created_at: string | null;
	tokens_used?: number | null;
	prompt_tokens?: number | null;
	completion_tokens?: number | null;
	tool_executions?: Array<{
		tool_name: string;
		arguments: Record<string, unknown>;
		result: { success: boolean; data: string; error?: string };
		duration_ms: number;
	}> | null;
}

export interface ChatSessionResponse {
	id: string;
	user_id: string;
	mode: string;
	title_generated: boolean;
	name: string | null;
	status: 'active' | 'accepted' | 'rejected';
	llm_config_id: string | null;
	original_text: string | null;
	created_at: string | null;
	updated_at: string | null;
	closed_at: string | null;
	message_count: number;
}

export interface ChatSessionWithMessagesResponse extends ChatSessionResponse {
	messages: ChatMessageResponse[];
	/** Present when a turn is streaming for this session; the client reattaches to it. */
	active_turn?: { turn_id: string; status: string } | null;
}

export interface SendChatMessageResponse {
	user_message: ChatMessageResponse;
	assistant_message: ChatMessageResponse;
	modified_prompt: string | null;
}

export interface ToolExecution {
	tool_name: string;
	arguments: Record<string, unknown>;
	result: { success: boolean; data: string; error?: string };
	duration_ms: number;
}

// Documentation feature (GET /api/docs/*)
export type DocLiveKind =
	| 'hooks'
	| 'field-types'
	| 'pipes'
	| 'output-types'
	| 'template-functions'
	| 'mcp-tools'
	| 'icons'
	| 'frontend-kit'
	| null;

// Docs 2.0 (#48/#49/#50): `type` stays 'markdown' | 'live' -- the RENDER kind
// (does this id have fetchable markdown, or a live component?) is unchanged.
// A typed doc's frontmatter kind rides a SEPARATE field, `doc_type`
// ('technique' | 'model' | null), so existing renderers keyed on `type`
// don't break. `status` mirrors the frontmatter's own status field so the
// tree can show it without a content fetch. Both fields are null for every
// doc that isn't a typed technique/model doc (everything today, and any
// plugin doc going forward), which renders exactly as before this feature
// existed.
export type DocDataType = 'technique' | 'model';
export type DocStatus = 'stable' | 'experimental' | 'needs-gpu-validation';

export interface DocItem {
	id: string;
	title: string;
	type: 'markdown' | 'live';
	live_kind: DocLiveKind;
	source: 'repo' | 'plugin';
	plugin_id: string | null;
	order: number;
	/** Optional sidebar grouping metadata. Flat items remain fully supported. */
	category?: string | null;
	category_order?: number | null;
	/** Typed-frontmatter kind; null for every untyped doc. */
	doc_type?: DocDataType | string | null;
	/** Present only for doc_type 'technique' | 'model' docs. */
	status?: DocStatus | null;
}

export interface DocSection {
	id: 'user' | 'developer' | 'contributor';
	title: string;
	items: DocItem[];
}

export interface DocTree {
	sections: DocSection[];
	/**
	 * Sections omitted from `sections` for this viewer (developer/contributor
	 * docs are admin-only) — id/title/count only, never content. Empty for
	 * admins. No UI consumes this yet: the docs surface lives inside Admin, so
	 * every current viewer is an admin and sees everything.
	 */
	hidden_sections?: { id: DocSection['id']; title: string; count: number }[];
}

export interface DocPaperRef {
	arxiv?: string | null;
	title?: string | null;
	url?: string | null;
}

export interface DocReferenceImpl {
	name?: string | null;
	url?: string | null;
	license?: string | null;
}

export interface DocKnob {
	key: string;
	surface: 'preset' | 'env' | 'admin' | string;
	/** Backend schema types this `Any` (pydantic) -- render with String(). */
	default?: unknown;
	effect: string;
}

/** Frontmatter shape for a doc of type 'technique' (src/core/docs/typed.py::TechniqueMeta). */
export interface TechniqueMeta {
	type?: string;
	title: string;
	category_group: string;
	status: DocStatus;
	families: string[];
	authors: string[];
	paper?: DocPaperRef | null;
	reference_impl?: DocReferenceImpl | null;
	knobs?: DocKnob[];
	related?: string[];
}

export interface ModelSpec {
	arch: string;
	params?: string | null;
	latent: string;
	vae: string;
	te: string;
	guidance: string;
	shift?: unknown;
	/** "native" | "diffusers" -- matches an "all-native" technique's families entry. */
	engine: string;
}

export interface ModelFile {
	role: string;
	dir: string;
	note?: string | null;
}

/** Frontmatter shape for a doc of type 'model' (src/core/docs/typed.py::ModelMeta). */
export interface ModelMeta {
	type?: string;
	title: string;
	family_key: string;
	modes: string[];
	spec: ModelSpec;
	files?: ModelFile[];
}

export interface DocModelRef {
	family_key: string;
	title: string;
	doc_id: string;
}

export interface DocTechniqueRef {
	slug: string;
	title: string;
	category_group: string;
	status: DocStatus;
	doc_id: string;
}

/** Resolved cross-references for a doc's `related`/`families` frontmatter
 * (technique docs) or the techniques that apply to it (model docs) -- the
 * server resolves slugs/family keys to titles + doc ids so the frontend never
 * has to cross-reference the tree itself. */
export interface DocRefs {
	models?: DocModelRef[];
	techniques?: DocTechniqueRef[];
}

export interface DocContent {
	id: string;
	title: string;
	markdown: string;
	/** Only present for type 'technique' | 'model' docs. */
	meta?: TechniqueMeta | ModelMeta | null;
	refs?: DocRefs | null;
}
