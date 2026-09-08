import { describe, expect, it } from 'vitest';
import {
	isDynamicMessageType,
	matchesOutputType,
	outputTypeFields,
	type OutputTypeEntry
} from './outputTypesReference';

const image: OutputTypeEntry = {
	key: 'image',
	output_class: 'ImageGenerationOutput',
	message_type: 'gallery_update',
	has_handler: true,
	has_serializer: true,
	description: 'An image a pipe produced.',
	fields: [
		{ name: 'image', type: 'Image', default: null },
		{ name: 'temporary', type: 'bool', default: true },
		{ name: 'seed', type: 'int', default: null }
	]
};

const progress: OutputTypeEntry = {
	key: 'progress',
	output_class: 'ProgressGenerationOutput',
	message_type: '<dynamic>',
	has_handler: false,
	has_serializer: true,
	fields: []
};

describe('outputTypeFields', () => {
	it('returns the entry fields', () => {
		expect(outputTypeFields(image)).toBe(image.fields);
	});

	it('defaults to an empty list when fields is absent', () => {
		expect(outputTypeFields({ key: 'x' })).toEqual([]);
	});
});

describe('isDynamicMessageType', () => {
	it('treats the "<dynamic>" placeholder as dynamic', () => {
		expect(isDynamicMessageType('<dynamic>')).toBe(true);
	});

	it('treats a real message type as not dynamic', () => {
		expect(isDynamicMessageType('gallery_update')).toBe(false);
	});

	it('treats an undefined message type as not dynamic', () => {
		expect(isDynamicMessageType(undefined)).toBe(false);
	});
});

describe('matchesOutputType', () => {
	it('matches on the key', () => {
		expect(matchesOutputType(image, 'imag')).toBe(true);
	});

	it('matches on the output class name', () => {
		expect(matchesOutputType(image, 'ImageGeneration')).toBe(true);
	});

	it('matches on the description', () => {
		expect(matchesOutputType(image, 'produced')).toBe(true);
	});

	it('matches on a field name, not just the key/class/description', () => {
		expect(matchesOutputType(image, 'seed')).toBe(true);
		expect(matchesOutputType(progress, 'seed')).toBe(false);
	});

	it('is case-insensitive', () => {
		expect(matchesOutputType(image, 'SEED')).toBe(true);
	});

	it('rejects a query matching neither the key, class, description nor any field', () => {
		expect(matchesOutputType(image, 'nonexistent')).toBe(false);
	});
});
