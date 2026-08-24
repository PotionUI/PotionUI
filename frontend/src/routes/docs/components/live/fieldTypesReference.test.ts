import { describe, expect, it } from 'vitest';
import {
	configurationOptions,
	isPluginSource,
	matchesFieldType,
	type FieldTypeEntry
} from './fieldTypesReference';

const slider: FieldTypeEntry = {
	type: 'slider',
	component: 'core:SliderField',
	has_options: false,
	container: false,
	source: 'core',
	configuration_schema: [
		{ name: 'min', param_type: 'float', default: 0, description: 'Minimum value for the slider' },
		{ name: 'max', param_type: 'float', default: 100, description: 'Maximum value for the slider' },
		{ name: 'step', param_type: 'float', default: 1, description: 'Step increment for the slider' },
		{ name: 'tooltip', param_type: 'str', default: '', description: 'Tooltip text next to the slider value' }
	]
};

const select: FieldTypeEntry = {
	type: 'select',
	component: 'core:SelectField',
	has_options: true,
	container: false,
	source: 'core',
	configuration_schema: []
};

describe('configurationOptions', () => {
	it('returns the entry configuration_schema', () => {
		expect(configurationOptions(slider)).toBe(slider.configuration_schema);
	});

	it('defaults to an empty list when configuration_schema is absent', () => {
		expect(configurationOptions({ type: 'string' })).toEqual([]);
	});
});

describe('isPluginSource', () => {
	it('treats the registry default "core" as not a plugin', () => {
		expect(isPluginSource('core')).toBe(false);
	});

	it('treats an undefined source as not a plugin', () => {
		expect(isPluginSource(undefined)).toBe(false);
	});

	it('treats any other source as a plugin', () => {
		expect(isPluginSource('spritesheet-plugin')).toBe(true);
	});
});

describe('matchesFieldType', () => {
	it('matches on the type name', () => {
		expect(matchesFieldType(slider, 'slid')).toBe(true);
	});

	it('matches on a configuration option name, not just the type name', () => {
		expect(matchesFieldType(slider, 'tooltip')).toBe(true);
		expect(matchesFieldType(select, 'tooltip')).toBe(false);
	});

	it('is case-insensitive', () => {
		expect(matchesFieldType(slider, 'TOOLTIP')).toBe(true);
	});

	it('rejects a query matching neither the type nor any option', () => {
		expect(matchesFieldType(slider, 'nonexistent')).toBe(false);
	});
});
