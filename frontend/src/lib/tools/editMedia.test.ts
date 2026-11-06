import { describe, expect, it } from 'vitest';
import { applyLookAvailability, editMediaAvailability, editMediaLabel, sliceSheetAvailability } from './editMedia';
import { buildFieldToolContext, buildLibraryToolContext } from './tools';

const item = (kind: 'image' | 'video' | 'audio' | 'mesh', id: string = kind) => ({
	id,
	kind,
	url: `/m/${id}`,
	filename: id
});

function ctx(...items: ReturnType<typeof item>[]) {
	const base = buildLibraryToolContext([], null);
	return { ...base, items, kinds: new Set(items.map((entry) => entry.kind)) };
}

describe('editMediaAvailability', () => {
	it('asks for a selection when there is none', () => {
		expect(editMediaAvailability(buildFieldToolContext(null))).toEqual({
			enabled: false,
			reason: 'Select an image or a video'
		});
	});

	it('accepts one or many images and videos', () => {
		expect(editMediaAvailability(ctx(item('image')))).toEqual({ enabled: true });
		expect(editMediaAvailability(ctx(item('image', 'a'), item('image', 'b')))).toEqual({ enabled: true });
		expect(editMediaAvailability(ctx(item('video')))).toEqual({ enabled: true });
		expect(editMediaAvailability(ctx(item('video'), item('image')))).toEqual({ enabled: true });
	});

	it('refuses audio, meshes, and a selection that holds one', () => {
		const reason = { enabled: false, reason: 'Only images and videos can be edited' };
		expect(editMediaAvailability(ctx(item('audio')))).toEqual(reason);
		expect(editMediaAvailability(ctx(item('mesh')))).toEqual(reason);
		expect(editMediaAvailability(ctx(item('image'), item('audio')))).toEqual(reason);
	});
});

describe('editMediaLabel', () => {
	it('names the editor the selection opens', () => {
		expect(editMediaLabel(ctx(item('image')))).toBe('Open in Image editor');
		expect(editMediaLabel(ctx(item('video')))).toBe('Open in Video editor');
		expect(editMediaLabel(ctx(item('video'), item('image')))).toBe('Open in editor');
		expect(editMediaLabel(ctx(item('audio')))).toBe('Open in editor');
	});
});

describe('applyLookAvailability', () => {
	it('is offered for any number of images and hidden for everything else', () => {
		expect(applyLookAvailability(ctx(item('image', 'a'), item('image', 'b')))).toEqual({ enabled: true });
		expect(applyLookAvailability(ctx(item('video')))).toEqual({ enabled: false, hidden: true });
		expect(applyLookAvailability(ctx(item('image'), item('video')))).toEqual({ enabled: false, hidden: true });
		expect(applyLookAvailability(ctx())).toEqual({ enabled: false, hidden: true });
	});
});

describe('sliceSheetAvailability', () => {
	const ctx = (kinds: string[]) => ({ items: kinds.map((kind, i) => ({ id: `i${i}`, kind })) }) as unknown as Parameters<typeof sliceSheetAvailability>[0];

	it('offers Slice into references for images only', () => {
		expect(sliceSheetAvailability(ctx(['image', 'image']))).toEqual({ enabled: true });
		expect(sliceSheetAvailability(ctx(['image', 'video']))).toEqual({ enabled: false, hidden: true });
		expect(sliceSheetAvailability(ctx([]))).toEqual({ enabled: false, hidden: true });
	});
});
