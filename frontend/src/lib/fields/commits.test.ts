import { describe, expect, it } from 'vitest';
import { fieldCommits, registerFieldComponent, unregisterFieldComponent, resolveFieldComponent } from './registry';
import { registerBuiltinFieldComponents } from './builtin';
import NumberBoxField from '$lib/components/form-fields/NumberBoxField.svelte';

describe('field commit declaration', () => {
	it('marks the core types that report a finished interaction themselves', () => {
		registerBuiltinFieldComponents();

		for (const type of ['slider', 'number', 'integer', 'stepper', 'string', 'textbox', 'boolean', 'checkbox', 'select', 'sampler', 'schedule', 'segmented', 'number_box']) {
			expect(fieldCommits(type), type).toBe(true);
		}
	});

	it('leaves types that never report one undeclared so the form falls back', () => {
		registerBuiltinFieldComponents();

		for (const type of ['seed', 'color', 'anchor', 'tags', 'model', 'unknown_type']) {
			expect(fieldCommits(type), type).toBe(false);
		}
	});

	it('treats a type registered without the flag as not committing', () => {
		registerFieldComponent('plugin_probe', { component: {} });
		expect(fieldCommits('plugin_probe')).toBe(false);
		unregisterFieldComponent('plugin_probe');
	});

	it('registers number_box onto NumberBoxField', async () => {
		registerBuiltinFieldComponents();
		expect(await resolveFieldComponent('number_box')).toBe(NumberBoxField);
	});
});
