import type { Condition, LogicalCondition } from '$lib/form/reactions';
import type { EditedMediaItem } from '$lib/services/api/media';

export type ToolMediaKind = 'image' | 'video' | 'audio';

export type ToolScope = 'library' | 'history' | 'field';

export type ToolCardinality = 'one_to_one' | 'one_to_many' | 'many_to_one';

export type ToolParamType =
	| 'integer'
	| 'float'
	| 'select'
	| 'checkbox'
	| 'color'
	| 'string'
	| 'anchor'
	| 'media'
	| (string & {});

export interface ToolParamOption {
	label: string;
	value: string | number;
}

export interface ToolParam {
	name: string;
	type: ToolParamType;
	label?: string;
	default?: unknown;
	min?: number;
	max?: number;
	step?: number;
	unit?: string;
	options?: Array<ToolParamOption | string | number>;
	allow_transparent?: boolean;
	when?: Condition | Condition[] | LogicalCondition;
	description?: string;
	widget?: string;
	placeholder?: string;
	accepts?: ToolMediaKind[];
}

export interface ToolRequirements {
	has_audio?: boolean;
	has_alpha?: boolean;
	min_duration?: number;
}

export interface ToolSelection {
	min?: number;
	max?: number;
}

export interface ToolDTO {
	id: string;
	label: string;
	icon: string;
	accepts: ToolMediaKind[];
	produces: 'same' | ToolMediaKind;
	cardinality: ToolCardinality;
	params: ToolParam[];
	requires: ToolRequirements;
	selection: ToolSelection | null;
	scopes: Array<ToolScope | 'editor'>;
	editor: string | null;
	preview: boolean;
	component: string | null;
	runnable: boolean;
	group?: 'edit' | 'effects' | 'audio';
	source: string;
	last_params: Record<string, unknown> | null;
}

export interface MediaToolItem extends EditedMediaItem {
	thumbnail_small?: string;
	thumbnail_medium?: string;
	thumbnail_large?: string;
}

export interface MediaToolStep {
	tool: string;
	params: Record<string, unknown>;
	hidden?: boolean;
}

export type MediaToolInputRef = { item_id: string } | { generation_file_id: string };

export type MediaToolOutputMode = 'new' | 'replace';

export type MediaToolSaveMode = MediaToolOutputMode | 'update';

export interface StartMediaToolRunBody {
	steps: MediaToolStep[];
	inputs: MediaToolInputRef[];
	output: { mode: MediaToolSaveMode; target_item_id?: string; collection_id?: string };
}

export type MediaToolRunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface MediaToolRunError {
	code: string;
	message: string;
}

export interface MediaToolRun {
	run_id: string;
	kind?: 'run' | 'preview';
	status: MediaToolRunStatus;
	progress: number;
	label: string;
	items: MediaToolItem[];
	replaced: boolean;
	preview?: MediaToolPreviewClip | null;
	error: MediaToolRunError | null;
}

export interface PreviewMediaToolBody {
	tool: string;
	params: Record<string, unknown>;
	input: MediaToolInputRef;
	time_seconds?: number;
}

export interface MediaToolSource {
	id: string;
	kind: ToolMediaKind | 'mesh';
	url: string;
	filename: string;
	width?: number | null;
	height?: number | null;
	durationSeconds?: number | null;
	fps?: number | null;
	hasAudio?: boolean | null;
	hasAlpha?: boolean | null;
	itemId?: string | null;
	storedPath?: string | null;
	generationId?: string;
	generationFileId?: string | null;
	paramIndex?: number;
}

export function isTerminalStatus(status: MediaToolRunStatus): boolean {
	return status === 'completed' || status === 'failed' || status === 'cancelled';
}

export interface FramePreviewBody {
	input: MediaToolInputRef;
	steps: MediaToolStep[];
	time_seconds?: number;
	max_side?: number;
	format?: 'webp' | 'png';
}

export interface FramePreviewResult {
	blob: Blob;
	cached: boolean;
}

export interface ClipPreviewBody {
	input: MediaToolInputRef;
	steps: MediaToolStep[];
	start_seconds?: number;
	duration_seconds?: number;
	height?: number;
}

export interface MediaToolPreviewClip {
	id: string;
	url: string;
	mime_type: string;
	duration_seconds: number | null;
	width: number | null;
	height: number | null;
}

export interface ClipPreviewStart {
	run_id: string | null;
	preview: MediaToolPreviewClip | null;
}

export interface MediaToolArtifact {
	id: string;
	media_type: ToolMediaKind;
	mime_type: string | null;
	url: string;
	width: number | null;
	height: number | null;
	duration_seconds: number | null;
	fps: number | null;
	size: number | null;
}
