import { describe, it, expect } from 'vitest';
import { templatesFor } from './templates';
import type { OrganizeCatalog } from '$lib/types/organize';

const INSTANCE_ACTIONS = ['assign_model_group', 'add_model_tags', 'set_model_type', 'civitai-provider.fetch_info'];

function catalog(actions: string[], facts: string[]): OrganizeCatalog {
	return {
		subjects: [],
		kinds: {},
		operators: {},
		facts: facts.map((key) => ({
			key,
			label: key,
			subjects: ['model'],
			kind: 'text',
			operators: ['is'],
			picker: null,
			options: null,
			has_options_endpoint: false,
			description: '',
			source: 'core',
			component: null,
			previewable: true
		})),
		actions: actions.map((key) => ({
			key,
			label: key,
			subjects: ['model'],
			config_schema: [],
			requires_admin: false,
			undoable: false,
			source: 'core',
			component: null
		}))
	};
}

describe('templatesFor', () => {
	it('gives the user page no instance template, with or without a catalog', () => {
		for (const cat of [null, catalog(['add_to_collection', ...INSTANCE_ACTIONS], ['name', 'source', 'model_type', 'base_model'])]) {
			const actions = templatesFor('model', cat, 'user').flatMap((t) => (t.rule.actions ?? []).map((a) => a.action));
			expect(actions.length).toBeGreaterThan(0);
			expect(actions.filter((a) => INSTANCE_ACTIONS.includes(a))).toEqual([]);
		}
	});

	it('gives the instance scope the two instance templates once the catalog offers their actions', () => {
		const cat = catalog(INSTANCE_ACTIONS, ['name', 'source', 'model_type']);
		expect(templatesFor('model', cat, 'instance').map((t) => t.key)).toEqual(['model-group-by-name', 'model-civitai']);
	});

	it('hides an instance template whose action the catalog does not offer, and shows none before the catalog loads', () => {
		expect(templatesFor('model', catalog(['assign_model_group'], ['name', 'source', 'model_type']), 'instance').map((t) => t.key)).toEqual([
			'model-group-by-name'
		]);
		expect(templatesFor('model', null, 'instance')).toEqual([]);
	});

	it('starts the name template with the separator-insensitive flag', () => {
		const [template] = templatesFor('model', catalog(INSTANCE_ACTIONS, ['name', 'source', 'model_type']), 'instance');
		expect(template.rule.conditions[0]).toEqual({ fact: 'name', operator: 'contains', value: 'krea2', ignore_separators: true });
	});
});
