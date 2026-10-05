export type CompareAxisValue = { value: unknown; label: string };

export type CompareAxis = {
	field: string;
	type: string;
	label: string;
	values: CompareAxisValue[];
};

export type CompareConfig = {
	armed: boolean;
	x: CompareAxis | null;
	y: CompareAxis | null;
	lockSeed: boolean;
};

export type GridCellStatus =
	| 'empty'
	| 'queued'
	| 'running'
	| 'completed'
	| 'failed'
	| 'cancelled'
	| 'deleted';

export type GridCell = {
	x: number;
	y: number;
	generationId: string | null;
	status: GridCellStatus;
	axisValues: Record<string, string>;
	seed: number | null;
	thumbnailUrl: string | null;
	mediaType: string | null;
	error: string | null;
	elapsedSeconds: number | null;
	progress: { step: number; total: number } | null;
	previewUrl: string | null;
};

export type ActiveGrid = {
	id: string | null;
	config: CompareConfig;
	cells: GridCell[];
	cols: number;
	rows: number;
};

export type ServerGridCell = {
	x: number;
	y: number;
	generation_id: string | null;
	status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'deleted';
	axis_values: Record<string, string>;
	seed: number | null;
	thumbnail_url: string | null;
	media_type: string | null;
	error: string | null;
	elapsed_seconds?: number | null;
};

export type ServerGrid = {
	id: string;
	preset_id: string;
	tab_id: string | null;
	x_axis: CompareAxis;
	y_axis: CompareAxis | null;
	lock_seed: boolean;
	status: 'running' | 'completed' | 'partial' | 'cancelled';
	created_at: string;
	cells: ServerGridCell[];
};

export type GridSettings = { confirm_above: number; hard_cap: number };

export const DEFAULT_GRID_SETTINGS: GridSettings = { confirm_above: 24, hard_cap: 100 };

export const MAX_AXIS_VALUES = 100;

export const PROMPT_AXIS_FIELD = '__prompt__';

export type AxisEditorKind =
	| 'chips'
	| 'number'
	| 'seed'
	| 'model'
	| 'lora'
	| 'resolution'
	| 'prompt'
	| 'checkbox'
	| 'plugin';

export type AxisOption = { value: unknown; label: string };

export type AxisCandidate = {
	field: string;
	label: string;
	type: string;
	group: string;
	editor: AxisEditorKind | null;
	unavailableReason: string | null;
	options: AxisOption[];
	min: number | null;
	max: number | null;
	step: number | null;
	currentValue: unknown;
	modelType: string | null;
	config: Record<string, unknown>;
};

export function emptyCompareConfig(): CompareConfig {
	return { armed: false, x: null, y: null, lockSeed: true };
}
