// Model Management Types

export interface ProviderInfo {
	id?: string;
	provider: string;
	provider_model_id?: string;
	provider_version_id?: string;
	name?: string;
	description?: string;
	tags: string[];
	nsfw: boolean;
	download_url?: string;
	created_at?: string;
	updated_at?: string;
}

export interface Tag {
	id: string;
	name: string;
	type: 'MODEL' | 'GENERATION';
	user_id?: string;
	created_at?: string;
	model_count?: number;
	generation_count?: number;
}

export interface ModelFile {
	id: string;
	file_type: string;
	url: string;
	thumbnail_small?: string;
	thumbnail_medium?: string;
	thumbnail_large?: string;
	file_size?: number;
	display_order?: number;
}

export interface Model {
	id: string;
	filename: string;
	file_path: string;
	file_size?: number;
	sha256?: string;
	model_type: 'checkpoint' | 'lora' | 'embedding' | 'upscaler' | 'vae' | 'controlnet' | 'adetailer' | 'text_encoder';
	created_at?: string;
	updated_at?: string;
	indexed_at?: string;
	description?: string;
	tags?: Tag[];
	providers?: ProviderInfo[];
	files?: ModelFile[];
	custom_name?: string | null;
	is_favorite?: boolean;
	/** Backends that can load this model. Empty does NOT mean "unavailable" unless
	 *  the list response's top-level `availability_indexed` is true - see docs/models.md. */
	backend_ids?: string[];
	/** Admin-set SHARED values, keyed by attribute definition `key` (trigger words
	 *  live here too, under the `triggers` key - see constants/modelMetadata.ts). */
	model_metadata?: Record<string, unknown>;
	/** The requesting user's own overlay for `per_user` attribute definitions,
	 *  keyed the same way as `model_metadata`. May be `{}`; absent for scopes that
	 *  never carry it. Effective value = user override ?? shared ?? definition default. */
	user_model_metadata?: Record<string, unknown>;
}

// Attribute definitions (GET/POST/PUT/DELETE /api/models/attributes) - the
// DB-backed replacement for the old static metadata-field schema.

export type AttributeFieldType = 'slider' | 'number' | 'text' | 'select' | 'checkbox' | 'tags';

export interface AttributeSelectOption {
	value: string;
	label: string;
}

export interface AttributeConfig {
	min?: number;
	max?: number;
	step?: number;
	options?: AttributeSelectOption[];
}

export interface AttributeDefinition {
	id: string;
	key: string;
	label: string;
	field_type: AttributeFieldType;
	/** Model types this attribute applies to; empty = every type. */
	model_types: string[];
	config: AttributeConfig;
	default_value: unknown;
	description?: string;
	/** Whether each user may set their own overlay value (PUT .../attributes/user). */
	per_user: boolean;
	/** Whether only admins may see/edit this definition's values at all. */
	admin_only: boolean;
	/** Built-in definition - key/field_type are immutable and it can't be deleted. */
	system: boolean;
	source: string;
}

// Per-backend model availability (GET /api/models/{model_id}/availability)

export type ModelAvailabilityConfidence = 'verified' | 'reported' | 'name_only' | 'conflict';

export interface ModelAvailabilityEntry {
	id: string;
	model_id: string;
	backend_id: string;
	backend_name: string;
	engine: string | null;
	/** Engine-native identifier: a filesystem path for `native`, a bare/relative name for `comfyui`. */
	ref: string;
	size?: number | null;
	confidence: ModelAvailabilityConfidence;
	/** Content sha256 THIS backend computed for its own copy. Set when `confidence`
	 *  is `'conflict'` - compare against the model's canonical `sha256`. */
	digest?: string | null;
	indexed_at?: string | null;
}

export interface ModelAvailabilityResponse {
	model_id: string;
	availability: ModelAvailabilityEntry[];
	/** Whether ANY backend has ever been indexed. Authoritative for this model - unlike
	 *  the list endpoint's `availability_indexed`, use this one once fetched. */
	indexed: boolean;
	/** Same filename reported at different byte sizes across backends - likely different weights. */
	size_conflict: boolean;
	/** At least one backend's own copy disagrees with the model's canonical digest.
	 *  That row is excluded from routing until the file is re-synced and re-indexed. */
	digest_conflict: boolean;
}

export interface ModelCollection {
	id: string;
	name: string;
	user_id?: string;
	parent_id: string | null;
	created_at: string;
	item_count: number;
}

export interface ModelType {
	type: string;
	count: number;
	size_bytes: number;
	size_mb: number;
	size_gb: number;
}

export interface ModelStats {
	total_models_db: number;
	total_files_fs: number;
	total_size_bytes: number;
	total_size_mb: number;
	total_size_gb: number;
	by_type: Record<
		string,
		{
			count: number;
			size_bytes: number;
			size_mb: number;
		}
	>;
	models_missing_hashes: number;
	models_without_civitai: number;
}

// Form Field Types

export interface ModelFieldValue {
	modelPath: string;
	tagFilters: string[];
}

export interface ModelFieldConfig {
	model_type?: 'checkpoint' | 'lora' | 'embedding' | 'upscaler' | 'vae' | 'controlnet' | 'adetailer' | 'text_encoder';
	allow_tag_filters?: boolean;
	limit?: number;
	placeholder?: string;
	tags?: string[]; // Default tag filters
	allow_info_modal?: boolean;
}

export interface LoraPickerItem {
	model: string;
	strength: number;
	/** Strength to restore when this row is re-enabled after being toggled off
	 * (strength: 0). Absent while the row is enabled - see loraStrength.ts. */
	saved_strength?: number;
}

export interface LoraPickerConfig {
	model_type?: 'lora';
	placeholder?: string;
	strength_min?: number;
	strength_max?: number;
	strength_step?: number;
	strength_default?: number;
	max_items?: number | null;
	allow_info_modal?: boolean;
	show_triggers?: boolean;
	allow_tag_filters?: boolean;
	tags?: string[]; // Default/preseeded tag filters
}
