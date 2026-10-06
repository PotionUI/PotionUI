import type { Cube, FilterStep, OpSpec } from './engine';

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
	steps: FilterStep[];
	kinds?: { colour: number; spatial: number };
	unavailable_ops: string[];
	needs_plugin: string | null;
	backend_ok?: boolean;
	revision: string;
}

export type FilterOpInfo = OpSpec;

export interface FilterCatalogData {
	schema?: number;
	filters: FilterItem[];
	ops: FilterOpInfo[];
	groups: string[];
	load_errors?: Record<string, string[]>;
}

export interface FilterDraft {
	name: string;
	description?: string;
	group?: string;
	intensity: number;
	steps: FilterStep[];
	source_id?: string;
}

export interface FilterPatch {
	name?: string;
	description?: string;
	intensity?: number;
	steps?: FilterStep[];
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
	steps: FilterStep[];
	cube: Cube | null;
}

export function toActiveFilter(item: FilterItem, cube: Cube | null): ActiveFilter {
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
