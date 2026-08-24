import { describe, it, expect } from 'vitest';
import { itemEditorFor, selectFace, toolbarTools, usesLanes, type FaceState } from './mediaLoaderFaces';
import { readMediaLoaderConfig } from './mediaLoaderConfig';

function state(overrides: Partial<FaceState> = {}): FaceState {
	return {
		multiple: false,
		uploading: false,
		compact: false,
		previewUrl: null,
		fileType: null,
		...overrides
	};
}

describe('selectFace', () => {
	it('shows the full empty face by default', () => {
		expect(selectFace(state())).toBe('empty-full');
	});

	it('shows the compact empty face in a compact host', () => {
		expect(selectFace(state({ compact: true }))).toBe('empty-compact');
	});

	it('shows the loaded face for each kind', () => {
		expect(selectFace(state({ previewUrl: '/u/a.png', fileType: 'image' }))).toBe('image');
		expect(selectFace(state({ previewUrl: '/u/a.mp4', fileType: 'video' }))).toBe('video');
		expect(selectFace(state({ previewUrl: '/u/a.wav', fileType: 'audio' }))).toBe('audio');
	});

	it('stays empty while the value has a url but no readable kind', () => {
		expect(selectFace(state({ previewUrl: '/u/a.bin', fileType: null }))).toBe('empty-full');
	});

	it('shows the uploading face while a single-item upload runs', () => {
		expect(selectFace(state({ uploading: true }))).toBe('uploading');
	});

	// The combination the old `{#if}` chain never actually considered: in
	// multi mode the items already added stay visible and reorderable while
	// the next one uploads, so the field keeps its own face.
	it('keeps the multi face while uploading', () => {
		expect(selectFace(state({ multiple: true, uploading: true }))).toBe('multi');
	});

	it('keeps the multi face even when a stale single preview is set', () => {
		expect(selectFace(state({ multiple: true, previewUrl: '/u/a.png', fileType: 'image' }))).toBe('multi');
	});
});

describe('toolbarTools', () => {
	const options = { allowInpaint: false, compact: false, canEmitMask: true };

	it('offers crop, full, swap and remove on an image', () => {
		expect(toolbarTools('image', options).map((t) => t.key)).toEqual(['crop', 'full', 'swap', 'remove']);
	});

	it('offers the mask tool only when the field allows inpainting', () => {
		expect(toolbarTools('image', { ...options, allowInpaint: true }).map((t) => t.key)).toContain('mask');
	});

	// A mask leaves through the `${name}_inpaint_mask` sibling channel; a host
	// that does not wire it would drop the mask silently.
	it('hides the mask tool when the host cannot carry a mask', () => {
		const tools = toolbarTools('image', { ...options, allowInpaint: true, canEmitMask: false });
		expect(tools.map((t) => t.key)).not.toContain('mask');
	});

	it('offers trim and frame extraction on a video', () => {
		expect(toolbarTools('video', options).map((t) => t.key)).toEqual(['trim', 'frame', 'full', 'swap', 'remove']);
	});

	it('offers trim and swap on audio, and no full-size view', () => {
		expect(toolbarTools('audio', options).map((t) => t.key)).toEqual(['trim', 'swap', 'remove']);
	});

	it('pushes remove away from the constructive tools', () => {
		const tools = toolbarTools('image', options);
		expect(tools.filter((t) => t.pushRight).map((t) => t.key)).toEqual(['remove']);
	});

	it('drops the labels in a compact host', () => {
		expect(toolbarTools('image', { ...options, compact: true }).every((t) => !t.showLabel)).toBe(true);
		expect(toolbarTools('image', options).find((t) => t.key === 'crop')?.showLabel).toBe(true);
	});
});

describe('itemEditorFor', () => {
	it('sends an image to crop and timed media to trim', () => {
		expect(itemEditorFor('image')).toEqual({ key: 'crop', title: 'Crop & frame' });
		expect(itemEditorFor('video')?.key).toBe('trim');
		expect(itemEditorFor('audio')?.title).toBe('Trim on waveform');
	});

	it('offers nothing for an unreadable item', () => {
		expect(itemEditorFor(null)).toBeNull();
	});
});

describe('usesLanes', () => {
	it('lanes a multi field that takes more than one kind', () => {
		expect(usesLanes(readMediaLoaderConfig({ accept: 'image/*,video/*', multiple: true }))).toBe(true);
	});

	it('keeps a single grid for one accepted kind', () => {
		expect(usesLanes(readMediaLoaderConfig({ accept: 'image/*', multiple: true }))).toBe(false);
	});

	it('never lanes a single-item field', () => {
		expect(usesLanes(readMediaLoaderConfig({ accept: 'image/*,video/*' }))).toBe(false);
	});
});
