import { describe, expect, it } from 'vitest';
import { indexFields } from './fieldIndex';
import { buildDraft, countSelected, filterFormulas, groupStates, nextCopyName, preselectedIds } from './groups';
import type { FormulaDeclaration } from './types';

const schema = {
	properties: {
		root: {
			children: [
				{ type: 'select', name: 'speed_profile', title: 'Speed', options: [{ label: 'Quality', value: 'quality' }, { label: 'Turbo', value: 'turbo' }] },
				{ type: 'slider', name: 'steps', title: 'Steps', audience: 'advanced', reactions: [{ when: { field: 'speed_profile', operator: 'equals', value: 'turbo' }, then: { set_value: 4 } }] },
				{ type: 'resolution', name: 'resolution', title: 'Resolution' },
				{ type: 'model', name: 'unet', title: 'Model' },
				{ type: 'seed', name: 'seed' }
			]
		}
	}
};

const declaration: FormulaDeclaration = {
	groups: [
		{ id: 'speed', label: 'Speed', fields: ['speed_profile', 'steps'] },
		{ id: 'size', label: 'Size', fields: ['resolution'] },
		{ id: 'models', label: 'Models', preselect: false, fields: ['unet'] }
	]
};

const index = indexFields(schema);
const defaults = { speed_profile: 'quality', steps: 24, resolution: '1344x768', unet: '' };

describe('groupStates', () => {
	it('flags groups that differ from the defaults and summarises values with option labels', () => {
		const states = groupStates(declaration, index, { speed_profile: 'turbo', steps: 4, resolution: '1344x768', unet: 'model:1' }, defaults);
		expect(states.map((s) => [s.id, s.changed])).toEqual([['speed', true], ['size', false], ['models', true]]);
		expect(states[0].summary).toBe('Turbo · 4');
		expect(states[0].rows[1].advanced).toBe(true);
	});

	it('pre-ticks changed groups except those that opt out of preselect', () => {
		const states = groupStates(declaration, index, { speed_profile: 'turbo', steps: 4, resolution: '1344x768', unet: 'model:1' }, defaults);
		expect(preselectedIds(states)).toEqual(['speed']);
	});

	it('leaves out groups with no current values', () => {
		const states = groupStates(declaration, index, { resolution: '1x1' }, defaults);
		expect(states.map((s) => s.id)).toEqual(['size']);
	});

	it('counts the settings in the chosen groups', () => {
		const states = groupStates(declaration, index, { speed_profile: 'turbo', steps: 4, resolution: 'a' }, defaults);
		expect(countSelected(states, new Set(['speed', 'size']))).toBe(3);
	});
});

describe('buildDraft', () => {
	const formData = { speed_profile: 'turbo', steps: 4, resolution: 'a', seed: 9, prompt: 'cat', steps_tagFilters: ['x'], seed_tagFilters: ['y'] };

	it('saves only fields of the chosen groups and their companions', () => {
		const draft = buildDraft({
			presetId: 'p', mode: 'video', variant: null, presetVersion: '1', name: '  Turbo  ',
			declaration, selected: new Set(['speed']), index, formData
		});
		expect(draft.name).toBe('Turbo');
		expect(draft.groups).toEqual([{ id: 'speed' }]);
		expect(draft.values).toEqual({ speed_profile: 'turbo', steps: 4, steps_tagFilters: ['x'] });
	});

	it('never includes prompt, seed or fields outside any group', () => {
		const draft = buildDraft({
			presetId: 'p', mode: 'video', variant: 'v', presetVersion: '1', name: 'All',
			declaration, selected: new Set(['speed', 'size', 'models']), index, formData
		});
		expect(Object.keys(draft.values)).not.toContain('prompt');
		expect(Object.keys(draft.values)).not.toContain('seed');
		expect(Object.keys(draft.values)).not.toContain('seed_tagFilters');
	});
});

describe('list helpers', () => {
	it('filters by name ignoring case', () => {
		const items = [{ name: 'Turbo, cached' }, { name: 'Small pass' }];
		expect(filterFormulas(items, 'TURBO')).toEqual([items[0]]);
		expect(filterFormulas(items, '  ')).toHaveLength(2);
	});

	it('numbers duplicate copies', () => {
		expect(nextCopyName('A', [])).toBe('A copy');
		expect(nextCopyName('A', ['A copy', 'A copy 2'])).toBe('A copy 3');
	});
});
