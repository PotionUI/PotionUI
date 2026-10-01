import { describe, expect, it } from 'vitest';
import { buildFieldToolContext } from '$lib/tools/tools';
import { editImageAvailability, editImageRequest } from './editImage';

const image = {
	id: 'i1',
	kind: 'image' as const,
	url: '/api/media/generations/g1/0.png',
	filename: '0.png',
	width: 640,
	height: 480
};

describe('editImageAvailability', () => {
	it('asks for a selection when there is none', () => {
		expect(editImageAvailability(buildFieldToolContext(null))).toEqual({
			enabled: false,
			reason: 'Select 1 image'
		});
	});

	it('accepts a single image', () => {
		expect(editImageAvailability(buildFieldToolContext(image))).toEqual({ enabled: true });
	});

	it('refuses media that is not an image', () => {
		expect(editImageAvailability(buildFieldToolContext({ ...image, kind: 'video' }))).toEqual({
			enabled: false,
			reason: 'Only images can be edited'
		});
	});
});

describe('editImageRequest', () => {
	it('opens the paint editor on the selected file', () => {
		expect(editImageRequest(buildFieldToolContext(image))).toEqual({
			kind: 'paint',
			source: {
				url: image.url,
				kind: 'image',
				fileName: '0.png',
				width: 640,
				height: 480
			},
			itemIndex: null
		});
	});

	it('returns nothing when the tool is unavailable', () => {
		expect(editImageRequest(buildFieldToolContext(null))).toBeNull();
	});

	it('leaves dimensions null when the item has none', () => {
		const request = editImageRequest(
			buildFieldToolContext({ ...image, width: undefined, height: undefined })
		);
		expect(request?.source.width).toBeNull();
		expect(request?.source.height).toBeNull();
	});
});
