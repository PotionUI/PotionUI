import { describe, it, expect } from 'vitest';
import { resolveInstallModelsTarget } from './installModelsTarget';
import type { RecipeSummary } from '$lib/services/api/recipes';

function recipe(id: string, presetIds: string[] = []): RecipeSummary {
	return {
		id,
		name: id,
		summary: '',
		description: '',
		engine: 'native',
		category: 'image',
		artifact_count: 0,
		total_download_bytes: null,
		preset_name: null,
		last_completed_at: null,
		source: 'marketplace',
		plugin_id: null,
		step_count: 0,
		preset_ids: presetIds
	};
}

describe('resolveInstallModelsTarget', () => {
	it('offers nothing to a non-admin', () => {
		expect(resolveInstallModelsTarget([recipe('a')], null, false)).toBeNull();
	});

	it('offers nothing when the catalog is unknown or empty', () => {
		expect(resolveInstallModelsTarget(null, null, true)).toBeNull();
		expect(resolveInstallModelsTarget([], null, true)).toBeNull();
	});

	it('deep-links to the one recipe that declares the preset', () => {
		const target = resolveInstallModelsTarget(
			[recipe('a', ['sdxl/base']), recipe('b', ['flux/dev'])],
			'flux/dev',
			true
		);
		expect(target).toEqual({ href: '/admin?tab=recipes&recipe=b', label: 'Install models' });
	});

	it('encodes a recipe id that needs it', () => {
		const target = resolveInstallModelsTarget([recipe('comfy/detect', ['p'])], 'p', true);
		expect(target?.href).toBe('/admin?tab=recipes&recipe=comfy%2Fdetect');
	});

	it('falls back to the catalog when several recipes declare the preset', () => {
		const target = resolveInstallModelsTarget(
			[recipe('a', ['sdxl/base']), recipe('b', ['sdxl/base'])],
			'sdxl/base',
			true
		);
		expect(target?.href).toBe('/admin?tab=recipes');
	});

	it('deep-links a single-recipe instance even with no preset in scope', () => {
		expect(resolveInstallModelsTarget([recipe('only')], null, true)?.href).toBe(
			'/admin?tab=recipes&recipe=only'
		);
	});

	it('falls back to the catalog when no recipe declares the preset', () => {
		const target = resolveInstallModelsTarget(
			[recipe('a', ['sdxl/base']), recipe('b', ['flux/dev'])],
			'wan/t2v',
			true
		);
		expect(target?.href).toBe('/admin?tab=recipes');
	});

	it('falls back to the catalog with several recipes and no preset in scope', () => {
		expect(resolveInstallModelsTarget([recipe('a'), recipe('b')], null, true)?.href).toBe(
			'/admin?tab=recipes'
		);
	});

	it('survives a recipe whose preset_ids the server omitted', () => {
		const bare = { ...recipe('a'), preset_ids: undefined as unknown as string[] };
		expect(resolveInstallModelsTarget([bare], 'sdxl/base', true)?.href).toBe(
			'/admin?tab=recipes&recipe=a'
		);
	});
});
