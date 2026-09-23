import { describe, it, expect } from 'vitest';
import type { Plugin } from '$lib/stores/plugins';
import {
	DEFAULT_PLUGIN_FILTERS,
	applyPluginFilters,
	pluginCategoryCounts,
	pluginFilterActiveCount,
	pluginFilterChips,
	pluginFiltersFromSearchParams,
	pluginFiltersToSearchParams,
	clearAllPluginFilters,
	clearPluginFilterChip,
	type PluginFilters
} from './pluginFilters';

function plugin(overrides: Partial<Plugin> & Pick<Plugin, 'id' | 'name'>): Plugin {
	return {
		version: '1.0.0',
		type: 'full-stack',
		manifest_path: `${overrides.id}/manifest.yml`,
		enabled: true,
		...overrides
	};
}

const plugins: Plugin[] = [
	plugin({ id: 'civitai-provider', name: 'CivitAI Provider', category: 'models', tags: ['marketplace'], enabled: true }),
	plugin({ id: 'huggingface-provider', name: 'HuggingFace Provider', category: 'models', type: 'backend-only', enabled: false }),
	plugin({ id: 'nvidia-rtx-upscale', name: 'NVIDIA RTX Upscale', category: 'generation', type: 'backend-only', enabled: true, state: 'error', error: 'Invalid manifest' }),
	plugin({ id: 'system-monitor', name: 'System Monitor', category: 'system', type: 'frontend-only', enabled: false })
];

describe('pluginFilters URL round-trip', () => {
	it('round-trips every non-default value', () => {
		const filters: PluginFilters = { q: 'civ', state: 'enabled', type: 'full-stack', sortBy: 'category' };
		const params = pluginFiltersToSearchParams(filters);
		expect(params.get('q')).toBe('civ');
		expect(params.get('state')).toBe('enabled');
		expect(params.get('type')).toBe('full-stack');
		expect(params.get('sort_by')).toBe('category');
		expect(pluginFiltersFromSearchParams(params)).toEqual(filters);
	});

	it('omits defaults from the URL', () => {
		expect(pluginFiltersToSearchParams(DEFAULT_PLUGIN_FILTERS).toString()).toBe('');
	});

	it('falls back to defaults for unknown values', () => {
		const params = new URLSearchParams('state=bogus&type=nope&sort_by=whatever');
		expect(pluginFiltersFromSearchParams(params)).toEqual(DEFAULT_PLUGIN_FILTERS);
	});
});

describe('applyPluginFilters', () => {
	it('filters by section (category)', () => {
		const result = applyPluginFilters(plugins, 'models', DEFAULT_PLUGIN_FILTERS);
		expect(result.map((p) => p.id)).toEqual(['civitai-provider', 'huggingface-provider']);
	});

	it('keeps everything for the "all" section', () => {
		const result = applyPluginFilters(plugins, 'all', DEFAULT_PLUGIN_FILTERS);
		expect(result).toHaveLength(4);
	});

	it('searches name, id, description and tags case-insensitively', () => {
		const result = applyPluginFilters(plugins, 'all', { ...DEFAULT_PLUGIN_FILTERS, q: 'MARKETPLACE' });
		expect(result.map((p) => p.id)).toEqual(['civitai-provider']);
	});

	it('filters by state: enabled excludes disabled and errored plugins', () => {
		const result = applyPluginFilters(plugins, 'all', { ...DEFAULT_PLUGIN_FILTERS, state: 'enabled' });
		expect(result.map((p) => p.id)).toEqual(['civitai-provider']);
	});

	it('filters by state: error', () => {
		const result = applyPluginFilters(plugins, 'all', { ...DEFAULT_PLUGIN_FILTERS, state: 'error' });
		expect(result.map((p) => p.id)).toEqual(['nvidia-rtx-upscale']);
	});

	it('filters by type', () => {
		const result = applyPluginFilters(plugins, 'all', { ...DEFAULT_PLUGIN_FILTERS, type: 'frontend-only' });
		expect(result.map((p) => p.id)).toEqual(['system-monitor']);
	});

	it('sorts by name by default', () => {
		const result = applyPluginFilters(plugins, 'all', DEFAULT_PLUGIN_FILTERS);
		expect(result.map((p) => p.name)).toEqual([
			'CivitAI Provider',
			'HuggingFace Provider',
			'NVIDIA RTX Upscale',
			'System Monitor'
		]);
	});

	it('sorts by state: enabled first, then disabled, then errored', () => {
		const result = applyPluginFilters(plugins, 'all', { ...DEFAULT_PLUGIN_FILTERS, sortBy: 'state' });
		expect(result.map((p) => p.id)).toEqual([
			'civitai-provider',
			'huggingface-provider',
			'system-monitor',
			'nvidia-rtx-upscale'
		]);
	});
});

describe('plugin filter chips', () => {
	it('emits chips for state and type and clears them by key', () => {
		const filters: PluginFilters = { ...DEFAULT_PLUGIN_FILTERS, state: 'enabled', type: 'backend-only' };
		expect(pluginFilterChips(filters)).toEqual([
			{ key: 'state', label: 'Enabled' },
			{ key: 'type', label: 'Backend-only' }
		]);
		expect(pluginFilterActiveCount(filters)).toBe(2);
		expect(clearPluginFilterChip(filters, 'state')).toEqual({ ...filters, state: '' });
		expect(clearPluginFilterChip(filters, 'unknown')).toBe(filters);
	});

	it('clear all keeps the query and the sort', () => {
		const filters: PluginFilters = { q: 'civ', state: 'enabled', type: 'backend-only', sortBy: 'category' };
		expect(clearAllPluginFilters(filters)).toEqual({ q: 'civ', state: '', type: '', sortBy: 'category' });
		expect(pluginFilterChips(DEFAULT_PLUGIN_FILTERS)).toEqual([]);
		expect(pluginFilterActiveCount(DEFAULT_PLUGIN_FILTERS)).toBe(0);
	});
});

describe('pluginCategoryCounts', () => {
	it('seeds every known category with zero and counts the rest, plus "all"', () => {
		const counts = pluginCategoryCounts(plugins);
		expect(counts.all).toBe(4);
		expect(counts.models).toBe(2);
		expect(counts.generation).toBe(1);
		expect(counts.system).toBe(1);
		expect(counts.media).toBe(0);
		expect(counts.workflow).toBe(0);
		expect(counts.developer).toBe(0);
		expect(counts.other).toBe(0);
	});
});
