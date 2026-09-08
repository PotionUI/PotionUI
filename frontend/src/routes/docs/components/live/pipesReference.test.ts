import { describe, expect, it } from 'vitest';
import {
	groupPipesByFamily,
	ioChipLabel,
	ioChipTitle,
	matchesPipe,
	pipeConfiguration,
	pipeDescription,
	pipeFamily,
	pipeFamilyAnchor,
	pipeFamilyLabel,
	pipeGroupFamily,
	pipeInputs,
	pipeKey,
	pipeLabel,
	pipeOutputs,
	pipeVariant,
	pipeVariantLabel,
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

const flux: PipeEntry = {
	name: 'generator/flux',
	description: 'Generates an image with Flux.'
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

describe('pipeVariantLabel', () => {
	it('humanizes the variant alone, without the family prefix', () => {
		expect(pipeVariantLabel(zImage)).toBe('Z Image');
	});

	it('is undefined for a flat pipe', () => {
		expect(pipeVariantLabel(flat)).toBeUndefined();
	});
});

describe('pipeGroupFamily / groupPipesByFamily', () => {
	it('groups a variant pipe under its family segment', () => {
		expect(pipeGroupFamily(zImage)).toBe('generator');
	});

	it('groups a flat pipe under its own full name', () => {
		expect(pipeGroupFamily(flat)).toBe('audio_trim');
	});

	it('sorts families alphabetically and pipes within a family by name', () => {
		const groups = groupPipesByFamily([zImage, flat, flux]);
		expect(groups.map((g) => g.family)).toEqual(['audio_trim', 'generator']);
		const generatorGroup = groups.find((g) => g.family === 'generator')!;
		expect(generatorGroup.pipes.map((p) => pipeLabel(p))).toEqual([
			'generator/flux',
			'generator/z_image'
		]);
	});

	it('gives a flat pipe a singleton group', () => {
		const groups = groupPipesByFamily([flat]);
		expect(groups).toEqual([{ family: 'audio_trim', pipes: [flat] }]);
	});
});

describe('pipeFamilyAnchor', () => {
	it('slugifies a family name into a stable anchor id', () => {
		expect(pipeFamilyAnchor('generator')).toBe('pipe-family-generator');
		expect(pipeFamilyAnchor('audio_trim')).toBe('pipe-family-audio-trim');
	});
});

describe('ioChipLabel', () => {
	it('appends [] for an array slot', () => {
		expect(ioChipLabel({ name: 'images', is_array: true })).toBe('images[]');
	});

	it('leaves a scalar slot name as-is', () => {
		expect(ioChipLabel({ name: 'image', is_array: false })).toBe('image');
		expect(ioChipLabel({ name: 'image' })).toBe('image');
	});
});

describe('ioChipTitle', () => {
	it('combines description and a required note', () => {
		expect(ioChipTitle({ description: 'The prompt text.' }, true)).toBe(
			'The prompt text. — required'
		);
	});

	it('falls back to just the description or just "required"', () => {
		expect(ioChipTitle({ description: 'The prompt text.' }, false)).toBe('The prompt text.');
		expect(ioChipTitle({}, true)).toBe('required');
	});

	it('is undefined when there is neither a description nor a required flag', () => {
		expect(ioChipTitle({}, false)).toBeUndefined();
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

	it('matches on an input or output io_type', () => {
		expect(matchesPipe(zImage, 'P_PROMPT')).toBe(true);
		expect(matchesPipe(zImage, 'IMAGE')).toBe(true);
		expect(matchesPipe(flat, 'IMAGE')).toBe(false);
	});

	it('matches on the family segment', () => {
		expect(matchesPipe(zImage, 'generator')).toBe(true);
		expect(matchesPipe(flat, 'generator')).toBe(false);
	});

	it('is case-insensitive', () => {
		expect(matchesPipe(zImage, 'Z_IMAGE')).toBe(true);
	});

	it('rejects a query matching neither the name, description nor any input', () => {
		expect(matchesPipe(zImage, 'nonexistent')).toBe(false);
	});
});
