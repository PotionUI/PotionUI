import type { FilterStepSpec, OpDef } from './engine';

export type FilterSource = 'builtin' | 'local' | 'plugin' | 'mine';

export interface FilterItem {
	id: string;
	name: string;
	description?: string | null;
	group: string;
	order: number;
	intensity: number;
	tags?: string[];
	source: FilterSource;
	plugin_id?: string | null;
	overrides?: boolean;
	owned: boolean;
	has_lut: boolean;
	lut_size?: number | null;
	lut_url?: string | null;
	steps: FilterStepSpec[];
	kinds?: { colour: number; spatial: number };
	unavailable_ops: string[];
	needs_plugin: string | null;
	backend_ok?: boolean;
	revision: string;
}

export interface FilterOpInfo extends OpDef {
	source?: string;
	plugin_id?: string | null;
}

export interface FilterCatalogData {
	filters: FilterItem[];
	ops: FilterOpInfo[];
	groups: string[];
}

export interface FilterDraft {
	name: string;
	description?: string;
	intensity: number;
	steps: FilterStepSpec[];
}

export interface FilterPatch {
	name?: string;
	description?: string;
	intensity?: number;
	steps?: FilterStepSpec[];
}

export interface ActiveFilter {
	id: string;
	name: string;
	source: FilterSource;
	owned: boolean;
	group: string;
	pluginId: string | null;
	description: string;
	hasLut: boolean;
	defaultIntensity: number;
	steps: FilterStepSpec[];
	cube: string | null;
}

export function toActiveFilter(item: FilterItem, cube: string | null): ActiveFilter {
	return {
		id: item.id,
		name: item.name,
		source: item.source,
		owned: item.owned,
		group: item.source === 'mine' ? 'Mine' : item.group,
		pluginId: item.plugin_id ?? null,
		description: item.description ?? '',
		hasLut: item.has_lut,
		defaultIntensity: item.intensity,
		steps: item.steps.map((step) => ({ ...step })),
		cube
	};
}
