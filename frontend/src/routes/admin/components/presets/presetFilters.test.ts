import { describe, it, expect } from 'vitest';
import type { PresetInfo } from '$lib/types/api';
import {
	DEFAULT_PRESET_FILTERS,
	applyPresetFilters,
	clearAllPresetFilters,
	clearPresetFilterChip,
	presetEngineCounts,
	presetEngines,
	presetFilterActiveCount,
	presetFilterChips,
	presetFiltersFromSearchParams,
	presetFiltersToSearchParams,
	presetSectionCounts,
	presetVramLabel,
	type PresetFilters
} from './presetFilters';

function preset(overrides: Partial<PresetInfo> = {}): PresetInfo {
	return {
		id: 'preset-1',
		name: 'SDXL',
		version: '1.0.0',
		tags: [],
		category: 'image',
		engine: 'native',
		installed: false,
		assignment_count: 0,
		group_count: 0,
		...overrides
	};
}

describe('presetFiltersFromSearchParams / presetFiltersToSearchParams', () => {
	it('round-trips a fully-set filter state through the URL', () => {
		const filters: PresetFilters = {
			q: 'qwen',
			engine: 'native',
			install: 'installed',
			requirements: 'missing',
			assignment: 'unassigned',
			sortBy: 'vram'
		};
		const params = presetFiltersToSearchParams(filters);
		expect(params.get('q')).toBe('qwen');
		expect(params.get('engine')).toBe('native');
		expect(params.get('install')).toBe('installed');
		expect(params.get('requirements')).toBe('missing');
		expect(params.get('assignment')).toBe('unassigned');
		expect(params.get('sort_by')).toBe('vram');
		expect(presetFiltersFromSearchParams(params)).toEqual(filters);
	});

	it('omits defaults from the URL so a clean grid has no query string', () => {
		expect(presetFiltersToSearchParams(DEFAULT_PRESET_FILTERS).toString()).toBe('');
	});

	it('falls back to the defaults for unknown install/requirements/assignment/sort values', () => {
		const params = new URLSearchParams({ install: 'pending', requirements: 'broken', assignment: 'mine', sort_by: 'usage' });
		const filters = presetFiltersFromSearchParams(params);
		expect(filters.install).toBe('all');
		expect(filters.requirements).toBe('all');
		expect(filters.assignment).toBe('any');
		expect(filters.sortBy).toBe('name');
	});
});

describe('applyPresetFilters', () => {
	const sdxl = preset({ id: 'sdxl', name: 'SDXL', category: 'image', engine: 'native', installed: true, tags: ['photorealistic'] });
	const qwen = preset({
		id: 'qwen',
		name: 'Qwen-Image-2.1',
		category: 'image',
		engine: 'native',
		installed: false,
		tags: ['qwen', 'text-rendering'],
		requirements_summary: { ok: 1, missing: 1, unknown: 0, optional_missing: 0 },
		requires: { recommended_vram_gb: 24 }
	});
	const h3 = preset({
		id: 'h3',
		name: 'MiniMax-H3-Fast',
		category: 'video',
		engine: 'comfyui',
		installed: true,
		assignment_count: 3,
		requires: { recommended_vram_gb: 32 }
	});
	const all = [sdxl, qwen, h3];

	it('narrows to the active sidebar section (category)', () => {
		expect(applyPresetFilters(all, DEFAULT_PRESET_FILTERS, 'video').map((p) => p.id)).toEqual(['h3']);
		expect(applyPresetFilters(all, DEFAULT_PRESET_FILTERS, 'all').map((p) => p.id).sort()).toEqual(['h3', 'qwen', 'sdxl']);
	});

	it('matches the search against name, id, and tags', () => {
		expect(applyPresetFilters(all, { ...DEFAULT_PRESET_FILTERS, q: 'qwen' }, 'all').map((p) => p.id)).toEqual(['qwen']);
		expect(applyPresetFilters(all, { ...DEFAULT_PRESET_FILTERS, q: 'text-rendering' }, 'all').map((p) => p.id)).toEqual(['qwen']);
		expect(applyPresetFilters(all, { ...DEFAULT_PRESET_FILTERS, q: 'h3' }, 'all').map((p) => p.id)).toEqual(['h3']);
	});

	it('filters by engine, install status, requirements, and assignment', () => {
		expect(applyPresetFilters(all, { ...DEFAULT_PRESET_FILTERS, engine: 'comfyui' }, 'all').map((p) => p.id)).toEqual(['h3']);
		expect(applyPresetFilters(all, { ...DEFAULT_PRESET_FILTERS, install: 'installed' }, 'all').map((p) => p.id).sort()).toEqual(['h3', 'sdxl']);
		expect(applyPresetFilters(all, { ...DEFAULT_PRESET_FILTERS, requirements: 'missing' }, 'all').map((p) => p.id)).toEqual(['qwen']);
		expect(applyPresetFilters(all, { ...DEFAULT_PRESET_FILTERS, assignment: 'unassigned' }, 'all').map((p) => p.id).sort()).toEqual(['qwen', 'sdxl']);
	});

	it('sorts by name and by VRAM, with undeclared VRAM sorting last', () => {
		expect(applyPresetFilters(all, DEFAULT_PRESET_FILTERS, 'all').map((p) => p.name)).toEqual([
			'MiniMax-H3-Fast',
			'Qwen-Image-2.1',
			'SDXL'
		]);
		expect(applyPresetFilters(all, { ...DEFAULT_PRESET_FILTERS, sortBy: 'vram' }, 'all').map((p) => p.id)).toEqual([
			'qwen',
			'h3',
			'sdxl'
		]);
	});

	it('does not mutate the list it was given', () => {
		const source = [sdxl, qwen, h3];
		applyPresetFilters(source, DEFAULT_PRESET_FILTERS, 'all');
		expect(source.map((p) => p.id)).toEqual(['sdxl', 'qwen', 'h3']);
	});
});

describe('preset filter chips', () => {
	it('emits one chip per active field and clears them individually', () => {
		const filters: PresetFilters = {
			q: 'x',
			engine: 'native',
			install: 'installed',
			requirements: 'unknown',
			assignment: 'unassigned',
			sortBy: 'vram'
		};
		expect(presetFilterChips(filters)).toEqual([
			{ key: 'engine', label: 'engine: native' },
			{ key: 'install', label: 'Installed' },
			{ key: 'requirements', label: 'Unknown requirements' },
			{ key: 'assignment', label: 'Unassigned' }
		]);
		expect(clearPresetFilterChip(filters, 'engine').engine).toBe('');
		expect(clearPresetFilterChip(filters, 'install').install).toBe('all');
		expect(clearPresetFilterChip(filters, 'nothing')).toBe(filters);
	});

	it('counts each active field as one filter and keeps query and sort when clearing all', () => {
		const filters: PresetFilters = {
			q: 'keep me',
			engine: 'native',
			install: 'installed',
			requirements: 'missing',
			assignment: 'unassigned',
			sortBy: 'vram'
		};
		expect(presetFilterActiveCount(filters)).toBe(4);
		expect(clearAllPresetFilters(filters)).toEqual({ ...DEFAULT_PRESET_FILTERS, q: 'keep me', sortBy: 'vram' });
		expect(presetFilterActiveCount(DEFAULT_PRESET_FILTERS)).toBe(0);
	});
});

describe('preset engine and section counts', () => {
	it('counts engines and categories across the whole catalog', () => {
		const presets = [
			preset({ id: 'a', category: 'image', engine: 'native' }),
			preset({ id: 'b', category: 'image', engine: 'native' }),
			preset({ id: 'c', category: 'video', engine: 'comfyui' })
		];
		expect(presetEngines(presets)).toEqual(['comfyui', 'native']);
		expect(presetEngineCounts(presets)).toEqual({ native: 2, comfyui: 1 });
		expect(presetSectionCounts(presets)).toEqual({ all: 3, image: 2, video: 1 });
	});
});

describe('presetVramLabel', () => {
	it('formats the recommended VRAM hint and omits it when undeclared', () => {
		expect(presetVramLabel({ requires: { recommended_vram_gb: 24 } })).toBe('24 GB VRAM');
		expect(presetVramLabel({ requires: {} })).toBeNull();
		expect(presetVramLabel({})).toBeNull();
	});
});
