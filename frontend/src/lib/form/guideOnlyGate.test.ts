import { describe, expect, it } from 'vitest';
import { processSchemaWithReactions } from './reactions';
import { isPromptlessMode } from '$lib/utils/promptlessMode';

const nullActions = { set_value: null, set_disabled: null, update_options: null, update_validation: null, set_filter_tags: null };

const guideOnlyWhen = {
	logic: 'AND',
	conditions: [
		{ field: 'guide_only', operator: 'equals', value: true, equals: true },
		{ field: 'guide_extract', operator: 'equals', value: true, equals: true },
		{ field: 'guide', operator: 'not_in', value: ['none', 'grayscale'], not_in: ['none', 'grayscale'] }
	]
};

const modelNames = ['diffusion_model', 'text_encoder', 'vae', 'control_model'];

const schema = {
	properties: {
		root: {
			children: [
				{
					type: 'section',
					title: 'Models',
					reactions: [{ when: guideOnlyWhen, then: { set_visibility: false, ...nullActions } }],
					children: modelNames.map((name) => ({ type: 'model', name, required: true }))
				}
			]
		}
	}
};

const presetVars = {
	promptless_modes: [
		{
			mode: 'control',
			when: {
				logic: 'AND',
				conditions: [
					{ field: 'guide_only', equals: true },
					{ field: 'guide_extract', equals: true },
					{ field: 'guide', not_in: ['none', 'grayscale'] }
				]
			}
		}
	]
};

function gate(formData: Record<string, unknown>) {
	const processed = processSchemaWithReactions(schema, formData).processedSchema;
	const section = processed.properties.root.children[0];
	return {
		promptless: isPromptlessMode(presetVars, 'control', formData),
		modelsShown: section.visible !== false
	};
}

const base = { guide: 'openpose', guide_extract: true, ...Object.fromEntries(modelNames.map((n) => [n, ''])) };

describe('control mode generate gate', () => {
	it('guide-only needs no prompt and hides the models it never loads', () => {
		expect(gate({ ...base, guide_only: true })).toEqual({ promptless: true, modelsShown: false });
	});

	it('control without guide-only needs a prompt and shows the models', () => {
		expect(gate({ ...base, guide_only: false })).toEqual({ promptless: false, modelsShown: true });
	});

	it('a guide-only toggle left on without an extracted guide still shows the models', () => {
		expect(gate({ ...base, guide: 'none', guide_only: true })).toEqual({ promptless: false, modelsShown: true });
		expect(gate({ ...base, guide_extract: false, guide_only: true })).toEqual({ promptless: false, modelsShown: true });
	});
});
