import { describe, it, expect } from 'vitest';
import type { Backend } from '$lib/services/admin-api';
import {
	DEFAULT_BACKENDS_FILTERS,
	applyBackendsFilters,
	backendEngineCounts,
	backendEngines,
	backendsFilterActiveCount,
	backendsFilterChips,
	clearAllBackendsFilters,
	clearBackendsFilterChip
} from './backendsFilters';

function backend(overrides: Partial<Backend> = {}): Backend {
	return {
		id: 'b1',
		name: 'Local worker',
		engine: 'native',
		driver: 'native.local',
		enabled: true,
		is_default: true,
		priority: 1,
		timeout_seconds: 300,
		scheduling_policy: 'fifo',
		scheduling_max_consecutive_same_model: 3,
		configured: true,
		...overrides
	};
}

const engineLabel = (engine: string) => (engine === 'comfyui' ? 'ComfyUI' : engine);

describe('applyBackendsFilters', () => {
	const local = backend({ id: 'local', name: 'Zebra worker', engine: 'native' });
	const remote = backend({ id: 'remote', name: 'Alpha worker', engine: 'native', driver: 'native.remote' });
	const comfy = backend({ id: 'comfy', name: 'Studio comfy', engine: 'comfyui', driver: 'comfyui' });
	const all = [local, remote, comfy];

	it('passes everything through and sorts by name when query is empty', () => {
		expect(applyBackendsFilters(all, DEFAULT_BACKENDS_FILTERS, engineLabel).map((b) => b.id)).toEqual([
			'remote',
			'comfy',
			'local'
		]);
	});

	it('matches the search against name and engine label', () => {
		const byName = applyBackendsFilters(all, { ...DEFAULT_BACKENDS_FILTERS, q: 'zebra' }, engineLabel);
		expect(byName.map((b) => b.id)).toEqual(['local']);

		const byEngine = applyBackendsFilters(all, { ...DEFAULT_BACKENDS_FILTERS, q: 'comfyui' }, engineLabel);
		expect(byEngine.map((b) => b.id)).toEqual(['comfy']);
	});

	it('filters down to the selected engine', () => {
		const filtered = applyBackendsFilters(all, { ...DEFAULT_BACKENDS_FILTERS, engine: 'comfyui' }, engineLabel);
		expect(filtered.map((b) => b.id)).toEqual(['comfy']);
	});

	it('does not mutate the list it was given', () => {
		const source = [...all];
		applyBackendsFilters(source, DEFAULT_BACKENDS_FILTERS, engineLabel);
		expect(source.map((b) => b.id)).toEqual(all.map((b) => b.id));
	});
});

describe('backendEngines and backendEngineCounts', () => {
	const local = backend({ id: 'local', engine: 'native' });
	const remote = backend({ id: 'remote', engine: 'native', driver: 'native.remote' });
	const comfy = backend({ id: 'comfy', engine: 'comfyui', driver: 'comfyui' });
	const all = [local, remote, comfy];

	it('lists the distinct engines, sorted', () => {
		expect(backendEngines(all)).toEqual(['comfyui', 'native']);
	});

	it('counts backends per engine', () => {
		expect(backendEngineCounts(all)).toEqual({ native: 2, comfyui: 1 });
	});
});

describe('backends filter chips', () => {
	it('has no chip for the default engine filter', () => {
		expect(backendsFilterChips({ q: 'x', engine: '', sortBy: 'name' })).toEqual([]);
		expect(backendsFilterActiveCount({ q: 'x', engine: '', sortBy: 'name' })).toBe(0);
	});

	it('shows a chip when an engine filter is active', () => {
		const filters = { q: '', engine: 'comfyui', sortBy: 'name' as const };
		expect(backendsFilterChips(filters)).toEqual([{ key: 'engine', label: 'engine: comfyui' }]);
		expect(backendsFilterActiveCount(filters)).toBe(1);
	});

	it('keeps query and sort when clearing all', () => {
		const filters = { q: 'keep me', engine: 'comfyui', sortBy: 'name' as const };
		expect(clearAllBackendsFilters(filters)).toEqual({ q: 'keep me', engine: '', sortBy: 'name' });
	});

	it('is a no-op when clearing an unknown chip key', () => {
		const filters = { q: 'x', engine: '', sortBy: 'name' as const };
		expect(clearBackendsFilterChip(filters, 'nothing')).toBe(filters);
	});
});
