import { describe, expect, it } from 'vitest';
import {
	matchesPipe,
	pipeConfiguration,
	pipeDescription,
	pipeFamily,
	pipeFamilyLabel,
	pipeInputs,
	pipeKey,
	pipeLabel,
	pipeOutputs,
	pipeVariant,
	type PipeEntry
} from './pipesReference';

const zImage: PipeEntry = {
	name: 'generator/z_image',
	description: 'Generates an image with Z-Image.\n\nInternal implementation notes nobody needs.',
	status: 'installed',
	inputs: [{ name: 'positive_prompt', io_type: 'P_PROMPT', required: true, is_array: false }],
	outputs: [{ name: 'image', io_type: 'IMAGE', is_array: false }],
	configuration: [
		{ name: 'steps', param_type: 'int', default: 25, min_value: 1, max_value: 100 }
	]
};

const flat: PipeEntry = {
	name: 'audio_trim',
	description: 'Trims silence from an audio track.'
};

describe('pipeKey', () => {
	it('prefers id over name', () => {
		expect(pipeKey({ id: 'p1', name: 'generator/z_image' }, 0)).toBe('p1');
	});

	it('falls back to name, then the index', () => {
		expect(pipeKey(zImage, 3)).toBe('generator/z_image');
		expect(pipeKey({}, 3)).toBe('3');
	});
});

describe('pipeLabel', () => {
	it('prefers name over id', () => {
		expect(pipeLabel(zImage)).toBe('generator/z_image');
	});

	it('falls back to "unknown"', () => {
		expect(pipeLabel({})).toBe('unknown');
	});
});

describe('pipeDescription', () => {
	it('trims a multi-paragraph description to its first paragraph', () => {
		expect(pipeDescription(zImage)).toBe('Generates an image with Z-Image.');
	});

	it('defaults to an empty string when absent', () => {
		expect(pipeDescription({})).toBe('');
	});
});

describe('pipeFamily / pipeVariant / pipeFamilyLabel', () => {
	it('splits a "<family>/<variant>" pipe name', () => {
		expect(pipeFamily(zImage)).toBe('generator');
		expect(pipeVariant(zImage)).toBe('z_image');
		expect(pipeFamilyLabel(zImage)).toBe('Generator · Z Image');
	});

	it('leaves a flat pipe name without a family/variant split', () => {
		expect(pipeFamily(flat)).toBeUndefined();
		expect(pipeVariant(flat)).toBeUndefined();
		expect(pipeFamilyLabel(flat)).toBeUndefined();
	});
});

describe('pipeInputs / pipeOutputs / pipeConfiguration', () => {
	it('return the entry lists', () => {
		expect(pipeInputs(zImage)).toBe(zImage.inputs);
		expect(pipeOutputs(zImage)).toBe(zImage.outputs);
		expect(pipeConfiguration(zImage)).toBe(zImage.configuration);
	});

	it('default to an empty list when absent', () => {
		expect(pipeInputs({})).toEqual([]);
		expect(pipeOutputs({})).toEqual([]);
		expect(pipeConfiguration({})).toEqual([]);
	});
});

describe('matchesPipe', () => {
	it('matches on the pipe name', () => {
		expect(matchesPipe(zImage, 'z_image')).toBe(true);
	});

	it('matches on the description', () => {
		expect(matchesPipe(zImage, 'Z-Image')).toBe(true);
	});

	it('matches on an input name, not just the name/description', () => {
		expect(matchesPipe(zImage, 'positive_prompt')).toBe(true);
		expect(matchesPipe(flat, 'positive_prompt')).toBe(false);
	});

	it('is case-insensitive', () => {
		expect(matchesPipe(zImage, 'Z_IMAGE')).toBe(true);
	});

	it('rejects a query matching neither the name, description nor any input', () => {
		expect(matchesPipe(zImage, 'nonexistent')).toBe(false);
	});
});
