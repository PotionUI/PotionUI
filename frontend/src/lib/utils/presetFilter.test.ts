import { describe, it, expect } from 'vitest';
import { availablePresetEngines, filterPresets } from './presetFilter';
import type { PresetInfo } from '$lib/types/api';

function preset(over: Partial<PresetInfo>): PresetInfo {
	return {
		id: over.id ?? 'id',
		name: over.name ?? 'Name',
		version: '1.0.0',
		tags: over.tags ?? [],
		...over
	} as PresetInfo;
}

const PRESETS: PresetInfo[] = [
	preset({ id: 'a', name: 'Krea-2 standard', engine: 'native', category: 'image', tags: ['turbo'] }),
	preset({ id: 'b', name: 'LTX-2', engine: 'comfyui', category: 'video' }),
	preset({ id: 'c', name: 'SDXL realistic', engine: 'native', category: 'image' }),
	preset({ id: 'd', name: 'No engine', category: 'image' })
];

describe('availablePresetEngines', () => {
	it('returns distinct engines, sorted, skipping blanks', () => {
		expect(availablePresetEngines(PRESETS)).toEqual(['comfyui', 'native']);
	});

	it('is empty for a list with no engines', () => {
		expect(availablePresetEngines([preset({ id: 'x' })])).toEqual([]);
	});
});

describe('filterPresets', () => {
	it('returns everything with no query and no engine', () => {
		expect(filterPresets(PRESETS, '', null)).toHaveLength(4);
	});

	it('narrows to a single engine', () => {
		const r = filterPresets(PRESETS, '', 'native');
		expect(r.map((p) => p.id)).toEqual(['a', 'c']);
	});

	it('excludes presets without an engine when an engine is pinned', () => {
		expect(filterPresets(PRESETS, '', 'comfyui').map((p) => p.id)).toEqual(['b']);
	});

	it('ANDs the engine filter with the text query', () => {
		// "image" matches a, c, d by category; pinning native drops d.
		expect(filterPresets(PRESETS, 'image', 'native').map((p) => p.id)).toEqual(['a', 'c']);
	});

	it('text query matches name, category, engine, or tag', () => {
		expect(filterPresets(PRESETS, 'ltx', null).map((p) => p.id)).toEqual(['b']);
		expect(filterPresets(PRESETS, 'video', null).map((p) => p.id)).toEqual(['b']);
		expect(filterPresets(PRESETS, 'comfyui', null).map((p) => p.id)).toEqual(['b']);
		expect(filterPresets(PRESETS, 'turbo', null).map((p) => p.id)).toEqual(['a']);
	});

	it('is case-insensitive and trims the query', () => {
		expect(filterPresets(PRESETS, '  KREA  ', null).map((p) => p.id)).toEqual(['a']);
	});
});
