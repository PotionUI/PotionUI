// Segment and prompt editor types

export interface ChipData {
	id: string;
	categoryPath: string;
	valueId: string;
	label: string;
	value: string;
	allValues: Array<{
		id: string;
		label: string;
		value: string;
		preview_file_id?: string;
	}>;
	shuffle: boolean;
	autoRegen: boolean;
}

export type RichSegmentType = 'content' | 'break';

/**
 * Persisted, channel-agnostic prompt composition.
 *
 * Editor ids, collapsed state, AI provenance, and library-source links are
 * deliberately absent. Library application always creates detached editor
 * copies from this shape.
 */
export interface RichSegment {
	type: RichSegmentType;
	content: string;
	chips: Record<string, ChipData>;
	enabled: boolean;
	name?: string | null;
	color?: string | null;
	description?: string | null;
}

/** Runtime editor shape. `id` is local and is never persisted in RichSegment. */
export interface Segment {
	id: string;
	content: string;
	chips?: Record<string, ChipData>; // JSON-serializable version
	type?: RichSegmentType;
	enabled?: boolean;
	name?: string | null;
	color?: string | null;
	description?: string | null;

	/** @deprecated Legacy editor/session compatibility; use `enabled`. */
	isDisabled?: boolean;
	/** @deprecated UI-only legacy state. Collapse never affects flattening. */
	isCollapsed?: boolean;
	/** @deprecated Renamed to `name`. */
	title?: string;

	/** Provenance: which Segment Template slot this editor segment was applied from. */
	template?: {
		id: string;
		name: string;
		slot: string;
		position: number;
	};
}

export type PromptUsageHint = 'positive' | 'negative';

export interface Prompt {
	id: string;
	name?: string | null;
	display_name: string;
	segments: RichSegment[];
	flattened_text: string;
	usage_hint?: PromptUsageHint | null;
	user_id?: string;
	source_provider?: string | null;
	source_id?: string | null;
	source_url?: string | null;
	model_id?: string | null;
	model_name?: string | null;
	base_model?: string | null;
	tags?: string[];
	heart_count?: number;
	like_count?: number;
	laugh_count?: number;
	cry_count?: number;
	comment_count?: number;
	nsfw?: boolean;
	embedded?: boolean;
	metadata?: Record<string, unknown>;
	created_at?: string;
	updated_at?: string;
	/** How many completed generations carried this prompt as their source (GET /api/prompts). */
	usage_count?: number;
	last_used_at?: string | null;
}

/** A completed generation that used a library prompt as its source
 *  (`GET /api/prompts/{id}/generations`). `files` follows the same shape
 *  history rows use, so thumbnail helpers built for history work unchanged. */
export interface PromptGenerationItem {
	id: string;
	preset_id: string | null;
	preset_name: string | null;
	created_at: string | null;
	files: import('$lib/types/history').GenerationFile[];
}

export interface SavedSegment extends Omit<RichSegment, 'name'> {
	id: string;
	name: string;
	category_id: string;
	tags: string[];
	user_id?: string;
	created_at?: string;
	updated_at?: string;
	category?: SegmentCategory;
	effective_color?: string | null;
}

export interface SegmentTemplate {
	id: string;
	name: string;
	description?: string | null;
	segments: RichSegment[];
	tags: string[];
	user_id?: string;
	created_at?: string;
	updated_at?: string;
}

export interface SegmentCategory {
	id: string;
	name: string;
	description: string;
	color: string;
	created_at?: string;
	updated_at?: string;
	user_id?: string;
}

export type CreatePromptInput = Pick<Prompt, 'name' | 'segments' | 'usage_hint'>;
export type ReplacePromptInput = CreatePromptInput;
export type CreateSavedSegmentInput = Omit<
	SavedSegment,
	'id' | 'user_id' | 'created_at' | 'updated_at' | 'category' | 'effective_color'
>;
export type UpdateSavedSegmentInput = CreateSavedSegmentInput;
export type CreateSegmentTemplateInput = Pick<SegmentTemplate, 'name' | 'description' | 'segments' | 'tags'>;
export type UpdateSegmentTemplateInput = CreateSegmentTemplateInput;

export const PRESET_COLORS = [
	{ name: 'Blue', value: '#3B82F6' },
	{ name: 'Green', value: '#10B981' },
	{ name: 'Purple', value: '#8B5CF6' },
	{ name: 'Red', value: '#EF4444' },
	{ name: 'Orange', value: '#F59E0B' },
	{ name: 'Pink', value: '#EC4899' },
	{ name: 'Indigo', value: '#6366F1' },
	{ name: 'Teal', value: '#14B8A6' }
];

// LLM Generation types
export interface LLMConfig {
	id: string;
	name: string;
	provider: string;
	model: string;
	enabled: boolean;
	is_default?: boolean;
	api_key?: string;
	base_url?: string;
	temperature?: number;
	max_tokens?: number;
	top_p?: number;
}

export interface PromptStyle {
	id: string;
	name: string;
	description?: string;
	system_prompt?: string;
	user_prompt_template?: string;
	examples?: string[];
}

export interface Command {
	id: string;
	name: string;
	description?: string;
	template: string;
	category?: string;
}

// Phrasebook types
export interface PhrasebookOption {
	id: string;
	label: string;
	value: string;
	categoryPath: string;
	allValues?: Array<{ id: string; label: string; value: string }>;
}
