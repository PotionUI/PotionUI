import type { GenerationSegment } from './api';

export interface SystemTag {
	tag: string;
	category: 'general' | 'character';
	confidence: number;
}

/** Auto-tagger rating scores (0..1 each); null when the file is not tagged yet. */
export interface RatingScores {
	general?: number;
	sensitive?: number;
	questionable?: number;
	explicit?: number;
}

export interface GenerationFile {
	id: number;
	file_path: string;
	file_type: string;
	file_size?: number;
	pipe_name?: string;
	is_final: boolean;
	/** Produced from another final file of this generation (e.g. an enhance pass). */
	is_derived?: boolean;
	created_at: string;
	width?: number;
	height?: number;
	/** Shared with video (migration 086). Also real for audio files saved by
	 *  `AudioGenerationOutputHandler` (from the pipe's own `output.duration`,
	 *  or probed via `media_probe.get_audio_duration_seconds` otherwise) -
	 *  `None`/absent for audio rows saved before that landed, same
	 *  "not determined" contract as video. */
	duration_seconds?: number;
	fps?: number;
	thumbnail_small?: string;
	thumbnail_medium?: string;
	thumbnail_large?: string;
	system_tags?: SystemTag[];
	rating_scores?: RatingScores | null;
	/** Server-side blur verdict: questionable+explicit crossed the admin threshold. */
	nsfw?: boolean;
	/** Mesh files only - the real container format (e.g. 'glb', 'ply'). */
	mesh_format?: string;
	/** Audio files only - track kind ('speech', 'mixed', 'vocal', 'instrumental').
	 *  WS-only by design (workbench_update/gallery_update payloads during a live
	 *  generation) - the persisted `File` record has no such column, so this is
	 *  always absent on a `GenerationHistoryItem` read back after reload. */
	track_type?: string;
	/** Audio files only - sample rate in Hz. Same WS-only, never-persisted caveat as `track_type`. */
	sample_rate?: number;
	/** Audio files only - channel count. Same WS-only, never-persisted caveat as `track_type`. */
	channels?: number;
}

export interface Tag {
	id: string;
	name: string;
	color: string;
	type: 'MODEL' | 'GENERATION' | 'UPLOAD';
	generation_count?: number;
	model_count?: number;
	upload_count?: number;
	created_at: string;
}

export interface GenerationHistoryItem {
	id: string;
	preset_id?: string | null;
	preset_name?: string;
	preset_version?: string;
	mode?: string;
	/** Resolved preset form variant this generation actually bound against
	 *  (docs/presets.md "Variants"). Null for pre-migration rows or when
	 *  resolution failed — treat as "nothing to restore", not an error. */
	form_name?: string | null;
	prompt_state?: Record<string, unknown>;
	form_data: Record<string, unknown>;
	status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
	progress: number;
	created_at: string;
	completed_at?: string;
	updated_at: string;
	error_message?: string;
	files: GenerationFile[];
	tags?: Tag[];
	rating: number;
	is_favorite: boolean;
	segments?: GenerationSegment[];
	/** Id of the backend instance that actually executed this generation. */
	backend_id?: string | null;
	/** Same value as `form_data.seed`, surfaced top-level for a stable contract.
	 *  `-1` is honest and means the generation asked to randomize — the seed
	 *  actually rolled for a `-1` submission is never persisted, so `-1`
	 *  round-trips as `-1` on reuse. */
	seed?: number | null;
}

/** The reusable-settings slice of an imported generation bundle — same shape
 *  the generate page needs to restore preset/mode/form, minus anything only a
 *  live `GenerationHistoryItem` has (no `backend_id`, no top-level `seed`). */
export interface ImportBundleReuse {
	preset_id: string;
	mode: string;
	form_name: string | null;
	form_data: Record<string, unknown>;
	prompt_state?: Record<string, unknown>;
}

export interface ImportBundleResult {
	reuse: ImportBundleReuse;
	preset_available: boolean;
	warnings: string[];
}

// A collection tree is scoped - every module's collections are separate
// (backend migration 137): History folders (generations), Library folders
// (uploads), and Prompts folders (saved prompts) never mix. Keep in sync with
// the backend's ALLOWED_SCOPES (src.features.collections.dto).
export type CollectionScope = 'history' | 'library' | 'prompts';

export interface Collection {
	id: string;
	name: string;
	user_id?: string;
	scope: CollectionScope;
	parent_id: string | null;
	created_at: string;
	item_count: number;
}

export type DatePreset = 'all' | 'today' | 'yesterday' | 'last_week' | 'last_month';

export type MediaType = 'all' | 'image' | 'video' | 'audio';

export type SortBy = 'created_at' | 'completed_at' | 'rating' | 'file_size';

export type SortDir = 'asc' | 'desc';

export type HistorySearchMode = 'keyword' | 'semantic';

export interface GenerationHistoryFilters {
	status: string;
	datePreset: DatePreset;
	dateFrom?: string;
	dateTo?: string;
	selectedTagIds: string[];
	mediaType: MediaType;
	search: string;
	/** Whether `search` runs as keyword full-text or semantic visual search. */
	searchMode: HistorySearchMode;
	minRating?: number;
	favoritesOnly?: boolean;
	mode?: string;
	presetId?: string;
	modelName?: string;
	collectionId?: string;
	// Segment phrasebook provenance filter
	usedPhrasebookValueId?: string;
	usedPhrasebookLabel?: string;
	/** Auto-tagger system-tag facet (exact tag name). */
	systemTag?: string;
	sortBy?: SortBy;
	sortDir?: SortDir;
}

export interface HistoryFacets {
	modes: Array<{ value: string; count: number }>;
	presets: Array<{ id: string; name: string; count: number }>;
	models: Array<{ name: string; count: number }>;
}

export interface HistoryPageState {
	generations: GenerationHistoryItem[];
	totalCount: number;
	loading: boolean;
	currentPage: number;
	itemsPerPage: number;
	filters: GenerationHistoryFilters;
	selectedGeneration: GenerationHistoryItem | null;
	selectedFileIndex: number;
	selectionMode: boolean;
	selectedGenerationIds: string[];
	availableTags: Tag[];
	facets: HistoryFacets;
}
