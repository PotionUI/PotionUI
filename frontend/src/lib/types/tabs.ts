import type { Segment } from '$lib/types/segments';
import type { AudioData } from '$lib/types/audio';
import type { VideoDirectorValue } from '$lib/types/videoDirector';
import type { MusicDirectorValue } from '$lib/types/musicDirector';
import type { VariablesMap, VariableRoll } from '$lib/utils/variableDefs';

// LocalStorage key for tab persistence
export const TABS_STORAGE_KEY = 'potionui_tabs_state';

// Panel layout for the generate page: two panes (form | workbench+prompts) or
// three panes (form | prompts | workbench). Per-tab so each tab keeps its own
// arrangement, persisted to localStorage alongside the rest of the tab state.
export type GenerationLayoutMode = 'two' | 'three';

// Type for persisted tab state. Started as UI chrome only (layout, colors,
// which session a tab points at) on the theory that actual content always
// round-tripped through a saved session (see comments on `Tab` below
// this was wrong for a tab that was never saved as a session — the fields
// below let that content survive a reload too. Restored through the SAME
// `buildSessionRestoreTabPatch`-adjacent paths as a session load where one
// exists (`tabs.ts`'s `createTabsStore`), so a tab with a `selectedSessionId`
// still gets overwritten by the server's copy once `restoreTabSessions` runs
// — these fields are only load-bearing for the unsaved-tab path.
export interface PersistedTab {
	id: string;
	name: string;
	selectedPreset: string | null;
	selectedMode: string | null;
	selectedVariant?: string | null;
	selectedSessionId: string | null;
	activeGenerationId: string | null;
	/** Ids of generations this tab has enqueued (pending or running) — used to
	 *  rehydrate the queue via GET /api/generations/queue?tab_id= on reload. */
	queuedGenerationIds?: string[];
	autoTagIds?: string[];
	autoCollectionIds?: string[];
	/** Per-tab generation-outcome sound toggles; new tabs seed from the
	 *  global default in `$lib/utils/soundSettings`. */
	soundOnComplete?: boolean;
	soundOnError?: boolean;
	color?: string | null;
	layoutMode?: GenerationLayoutMode;
	promptPanelWidth?: number;
	positiveSegmentsCollapsed?: boolean;
	negativeSegmentsCollapsed?: boolean;
	/** Per-section fold state for `type: section` form fields; see the
	 *  matching field on `SessionData`/`Tab`. */
	sectionCollapsed?: Record<string, boolean>;
	workbenchMaxHeight?: string;
	leftPanelWidth?: number;
	leftPanelCollapsed?: boolean;
	// Content fields — the actual prompt/form state, persisted so an unsaved
	// tab survives a reload. `formData`/`variables` are sanitized on save
	// (see `sanitizeForPersistence` in tabPersistence.ts) to drop any
	// `data:`-URI string a field might hold, so no image/media blob ever
	// lands in localStorage.
	prompt?: string;
	negativePrompt?: string;
	promptSegments?: Segment[];
	negativePromptSegments?: Segment[];
	promptTabs?: PromptTabData[];
	activePromptTab?: number;
	promptRelay?: PromptRelayValue;
	videoDirector?: VideoDirectorValue;
	musicDirector?: MusicDirectorValue;
	formData?: Record<string, unknown>;
	variables?: VariablesMap;
	modeStateByMode?: Record<string, ModeState>;
	seed?: number;
	selectedBackendId?: string | null;
}

export interface PersistedTabsState {
	tabs: PersistedTab[];
	activeTabId: string;
}

export interface ImageData {
	url: string;
	originalUrl?: string;
	derived?: boolean;
	seed?: number;
	resolution?: [number, number];
	sampler?: string;
	clip_skip?: number;
	cfg?: number;
	denoise?: number;
	step?: number;
}

export interface VideoData {
	url: string;
	originalUrl?: string;
	derived?: boolean;
	duration?: number;
	fps?: number;
	resolution?: [number, number];
	seed?: number;
	sampler?: string;
	clip_skip?: number;
	cfg?: number;
	denoise?: number;
	step?: number;
	motion_strength?: number;
}

export interface MeshData {
	url: string;
	originalUrl?: string;
	file_type?: 'mesh';
	mesh_format?: string;
	mesh_name?: string;
	derived?: boolean;
	vertex_count?: number;
	face_count?: number;
	seed?: number;
}

export interface ProgressData {
	step: string;
	/** `null`/`undefined` means this stage hasn't reported a fraction yet (e.g. a cold model load) - render indeterminate, not 0%. */
	progress: number | null;
	message?: string;
	current_step?: string;
	pipe_id?: number;
	pipe_name?: string;
}

/** Shape of a running/completed generation object from the WebSocket or API. */
export interface ActiveGeneration {
	id?: string;
	generation_id?: string;
	status?: string;
	progress?: number;
	image?: string;
	path?: string;
	temporary?: boolean;
	message?: string;
	errorDetail?: string | null;
	[key: string]: unknown;
}

/** A pipe artifact received from the WebSocket. */
export interface PipeArtifact {
	pipe_id?: number;
	pipe_name?: string;
	artifact_type: string;
	artifact_data: Record<string, unknown>;
}

/** One outstanding (pending or running) generation this tab has enqueued on the backend queue. */
export interface QueuedGeneration {
	generation_id: string;
	queue_position: number | null;
	status: 'pending' | 'running';
}

export interface GenerationState {
	isGenerating: boolean;
	currentGeneration: ActiveGeneration | null;
	currentProgress: ProgressData | null;
	pipeTimers: Record<string, number>;
	/** Client epoch in milliseconds. Kept per tab so elapsed time survives tab switches. */
	startedAt: number | null;
	totalTime: number | null;
	/**
	 * Duration of the last COMPLETED generation in this tab, in milliseconds —
	 * unlike `totalTime` (reset to null the moment the next generation starts,
	 * so the live `elapsed` readout has a clean slate), this persists across
	 * the next run so the bar's `last` readout can show it throughout.
	 */
	lastDurationMs: number | null;
	batchImages: ImageData[];
	batchVideos: VideoData[];
	batchAudios: AudioData[];
	batchMeshes?: MeshData[];
	artifacts: PipeArtifact[];
	workbenchIndex: number;
	workbenchTotal: number;
	/** Latest message per plugin-declared `generation.output` message type (A5 renderer extension). */
	pluginOutputs?: Record<string, { msg: unknown; pluginId: string; asset: string }>;
	/** Generations this tab currently has queued (pending or running) on the backend queue. */
	queue: QueuedGeneration[];
	/**
	 * The exact pre-expansion prompt template (`{a|b}`/`${var}` intact) submitted
	 * for this generation — captured once at submit time, in `startGeneration()`,
	 * from the same `prompts[0]` the request actually sent. Only the FIRST
	 * authored prompt pair is ever treated as a template by the backend
	 * (`src/features/generation/prompt_expansion.py`: "Only the first authored
	 * pair is a template"), so this is the one template every `rendered_prompt`
	 * artifact's per-image text was expanded from, regardless of image index.
	 * Captured rather than read live off the tab so it can't drift if the user
	 * edits the prompt (or a forever-mode loop re-submits) while this
	 * generation is still in flight. `null` before any submission, or when the
	 * mode bypasses expansion entirely (Video Director / Prompt Relay) — the
	 * artifact card falls back to plain rendering in that case.
	 */
	submittedPromptTemplate: { positive: string; negative: string } | null;
}

export interface PromptTabData {
	promptSegments: Segment[];
	negativePromptSegments: Segment[];
	prompt: string;
	negativePrompt: string;
}

/** The prompt- and form-editor slice of a tab, snapshotted per preset mode so
 *  switching modes swaps this content instead of sharing one set across all
 *  of them — see `Tab.modeStateByMode` and `utils/modeState.ts`. */
export interface ModeState {
	prompt: string;
	negativePrompt: string;
	promptSegments: Segment[];
	negativePromptSegments: Segment[];
	promptTabs?: PromptTabData[];
	activePromptTab?: number;
	formData: Record<string, unknown>;
}

export interface PromptRelaySegment {
	id: string;
	start: number;
	end: number;
	text: string;
}

export interface MediaRef {
	path: string;
	relative_path?: string;
	url?: string;
	name?: string;
	type?: string;
	/** User-set handle for a multi-item media field's entry (MediaLoaderField
	 *  `multi: true`) - other systems (e.g. a `<Picture N>` prompt binding)
	 *  reference an item by this, so it's absent, not empty, when unset. */
	label?: string;
}

export interface PromptRelayImageSegment {
	id: string;
	start: number;
	strength: number;
	media: MediaRef | null;
}

export interface PromptRelayAudioSegment {
	id: string;
	start: number;
	trimStart: number;
	length: number;
	media: MediaRef | null;
}

export interface PromptRelayTimeline {
	duration: number;
	fps: number;
	segments: PromptRelaySegment[];
	imageSegments?: PromptRelayImageSegment[];
	audioSegments?: PromptRelayAudioSegment[];
}

export interface PromptRelayValue {
	global_prompt: string;
	timeline: PromptRelayTimeline;
}

export interface Tab {
	id: string;
	name: string;
	selectedPreset: string | null;
	selectedMode: string | null;
	/** Name of the selected mode's preset variant (form). Also persisted to
	 *  localStorage so an unsaved tab keeps its variant across a
	 *  reload; either way it's re-validated against the mode's live variants
	 *  once modes load (see the `modesPerTab` reactive block in
	 *  `routes/generate/+page.svelte`), falling back to the mode's default
	 *  if the restored value no longer exists. */
	selectedVariant?: string | null;
	selectedSessionId?: string | null;
	/**
	 * In-memory signature of the last server-saved payload. The generate page
	 * unmounts inactive tabs, so the session bar cannot keep this only in
	 * component state. It deliberately is not persisted: a page reload always
	 * rehydrates a selected session from the server.
	 */
	savedSessionSignature?: string | null;
	/** One-shot marker, deliberately absent from PersistedTab. */
	sessionBaselineAwaitingFormNormalization?: boolean;
	prompt: string;
	negativePrompt: string;
	promptSegments?: Segment[];
	negativePromptSegments?: Segment[];
	// Multi-prompt support
	promptTabs?: PromptTabData[];
	activePromptTab?: number;
	// Prompt-relay support (timeline of per-segment prompts in the prompt section)
	promptRelay?: PromptRelayValue;
	// Video Director support (structured multi-mode video composition editor).
	// Round-trips via sessions/prompt_state, and via localStorage tab
	// persistence for an unsaved tab.
	videoDirector?: VideoDirectorValue;
	// Music Director support (structured multi-mode song composition editor).
	// Round-trips via sessions/prompt_state, and via localStorage tab
	// persistence for an unsaved tab -- same lifecycle as `videoDirector`.
	musicDirector?: MusicDirectorValue;
	formData: Record<string, unknown>;
	/** Prompt variables (name -> typed definition), referenced from any segment of
	 *  this tab as `${name}`. Shared by all segments/prompt-tabs. Like `formData`,
	 *  this round-trips via sessions (see SessionData.variables) and, for an
	 *  unsaved tab, via localStorage tab persistence. A variable
	 *  has a type (`text` | `choice`); see utils/variableDefs.ts for the shape and
	 *  the pure conversion down to the unchanged `Record<string,string>` wire
	 *  format a GenerationRequest actually sends. */
	variables?: VariablesMap;
	/** The last client-side roll for each `shuffle`-mode choice
	 *  variable, keyed by name — RUN state, not definition state (see
	 *  variableDefs.ts `VariablesSubmitResult.rolls`). Deliberately separate
	 *  from `variables`/`SessionData` and absent from PersistedTab: it's what
	 *  a Generate click actually used, not something the user configured, so
	 *  it doesn't belong in a saved session and doesn't survive a reload —
	 *  same tier as `Tab.generation`. Usage chips re-render from this. */
	variableRolls?: Record<string, VariableRoll>;
	/** Snapshot of the prompt/segment/form content for every preset mode this
	 *  tab has visited other than the currently active one — the active
	 *  mode's own content lives in `prompt`/`promptSegments`/`formData`/etc.
	 *  directly. Populated and consumed by `utils/modeState.ts` on mode
	 *  switch, and by the session save/load paths (`SessionPill.svelte`,
	 *  `+page.svelte`'s `restoreTabSessions`) so a save captures every visited
	 *  mode's configuration, not just the active one, and a load re-seeds
	 *  this cache for every mode the loaded session has data for. Also
	 *  persisted to localStorage so an unsaved tab's other visited
	 *  modes survive a reload too; has no dedicated key of its own in
	 *  `SessionData` — a session's per-mode data already covers the same
	 *  fields under their own names. */
	modeStateByMode?: Record<string, ModeState>;
	seed?: number;
	selectedBackendId?: string | null;
	activeGenerationId?: string | null;
	/** Library prompt (src/features/prompt_database) applied to this tab from the
	 *  Prompt Library's "Generate with this" — rides onto GenerationRequest.source_prompt_id.
	 *  Cleared on preset change; deliberately absent from PersistedTab, same tier as
	 *  `savedSessionSignature`. */
	sourcePromptId?: string | null;
	generation: GenerationState;
	workbenchMaxHeight: string;
	leftPanelWidth: number;
	/** Whether the generation settings form is folded to its compact rail. */
	leftPanelCollapsed?: boolean;
	// Per-tab panel layout (two/three panes) and the three-pane prompt pane width.
	layoutMode: GenerationLayoutMode;
	promptPanelWidth: number;
	/** Per-tab prompt composer layout, also stored in generation sessions. */
	positiveSegmentsCollapsed?: boolean;
	negativeSegmentsCollapsed?: boolean;
	/** See the matching field on `PersistedTab` above. */
	sectionCollapsed?: Record<string, boolean>;
	autoTagIds?: string[];
	autoCollectionIds?: string[];
	/** See the matching field on `PersistedTab` above. */
	soundOnComplete?: boolean;
	soundOnError?: boolean;
	color?: string | null;
}
