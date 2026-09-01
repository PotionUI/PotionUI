import { describe, it, expect } from 'vitest';
import {
	galleryTotal,
	galleryItemAt,
	firstGalleryEntry,
	galleryItemUrl,
	entryFileType,
	workbenchActionsFor,
	downloadExtensionFor,
	type WorkbenchBatches
} from './workbenchGallery';

const image = { url: '/img/0.png', originalUrl: '/img/0.png' };
const video = { url: '/vid/0.mp4', originalUrl: '/vid/0.mp4', file_type: 'video' };
const audio = { url: '/aud/0.wav', originalUrl: '/aud/0.wav', file_type: 'audio' as const };
const mesh = { url: '/mesh/0.glb', originalUrl: '/mesh/0.glb', file_type: 'mesh' as const, mesh_format: 'glb' };

describe('galleryTotal / galleryItemAt', () => {
	it('addresses the images, videos, audios, meshes chain as one absolute index', () => {
		const batches: WorkbenchBatches = {
			images: [image],
			videos: [video],
			audios: [audio],
			meshes: [mesh]
		};

		expect(galleryTotal(batches)).toBe(4);
		expect(galleryItemAt(batches, 0)).toEqual({ item: image, kind: 'image', index: 0 });
		expect(galleryItemAt(batches, 1)).toEqual({ item: video, kind: 'video', index: 1 });
		expect(galleryItemAt(batches, 2)).toEqual({ item: audio, kind: 'audio', index: 2 });
		expect(galleryItemAt(batches, 3)).toEqual({ item: mesh, kind: 'mesh', index: 3 });
	});

	it('reaches a mesh item even when the earlier buckets are empty', () => {
		const batches: WorkbenchBatches = { images: [], videos: [], audios: [], meshes: [mesh] };
		expect(galleryTotal(batches)).toBe(1);
		expect(galleryItemAt(batches, 0)).toEqual({ item: mesh, kind: 'mesh', index: 0 });
	});

	it('returns null past the end of the chain', () => {
		const batches: WorkbenchBatches = { images: [image], videos: [], audios: [], meshes: [] };
		expect(galleryItemAt(batches, 1)).toBeNull();
	});

	it('returns null for a negative or non-integer index', () => {
		const batches: WorkbenchBatches = { images: [image], videos: [], audios: [], meshes: [] };
		expect(galleryItemAt(batches, -1)).toBeNull();
		expect(galleryItemAt(batches, 0.5)).toBeNull();
	});

	it('treats missing/null buckets as empty rather than throwing', () => {
		const batches: WorkbenchBatches = { images: null, meshes: [mesh] };
		expect(galleryTotal(batches)).toBe(1);
		expect(galleryItemAt(batches, 0)?.kind).toBe('mesh');
	});

	it('firstGalleryEntry returns null for an empty generation', () => {
		expect(firstGalleryEntry({})).toBeNull();
	});
});

describe('entryFileType', () => {
	it('normalizes an UPPERCASE explicit file_type on the item', () => {
		const entry = { item: { file_type: 'MESH' }, kind: 'image' as const, index: 0 };
		expect(entryFileType(entry)).toBe('mesh');
	});

	it('falls back to the bucket kind when the item carries no file_type (images/videos from mapGenerationFiles)', () => {
		const entry = { item: {}, kind: 'image' as const, index: 0 };
		expect(entryFileType(entry)).toBe('image');
	});

	it('falls back to a mesh bucket kind too, not just image', () => {
		const entry = { item: {}, kind: 'mesh' as const, index: 0 };
		expect(entryFileType(entry)).toBe('mesh');
	});

	it('returns an empty string for a null entry', () => {
		expect(entryFileType(null)).toBe('');
	});
});

describe('galleryItemUrl', () => {
	it('prefers originalUrl over url', () => {
		expect(galleryItemUrl({ url: '/a', originalUrl: '/b' })).toBe('/b');
	});

	it('falls back to url when there is no originalUrl', () => {
		expect(galleryItemUrl({ url: '/a' })).toBe('/a');
	});

	it('returns null for a non-object item', () => {
		expect(galleryItemUrl(null)).toBeNull();
		expect(galleryItemUrl(undefined)).toBeNull();
	});
});

describe('workbenchActionsFor a mesh', () => {
	it('offers download, open-in-new-tab and expand but withholds the raster-only actions', () => {
		const actions = workbenchActionsFor('MESH');
		expect(actions.canDownload).toBe(true);
		expect(actions.canOpenInNewTab).toBe(true);
		// Expand renders through MeshPreview's own interactive viewer, not the
		// modal's <img>/<video> pair - see Workbench.svelte's mesh branch.
		expect(actions.canExpand).toBe(true);
		expect(actions.canCompare).toBe(false);
		expect(actions.canZoom).toBe(false);
		expect(actions.canCopyImage).toBe(false);
		expect(actions.hasPixelMetadata).toBe(false);
	});

	it('is case-insensitive the same way the display gate is', () => {
		expect(workbenchActionsFor('mesh')).toEqual(workbenchActionsFor('MESH'));
	});
});

describe('downloadExtensionFor a mesh', () => {
	it('prefers the URL suffix when the URL already has one', () => {
		expect(downloadExtensionFor('mesh', { url: '/x/0.obj', mesh_format: 'glb' })).toBe('obj');
	});

	it('falls back to mesh_format when the URL has no usable suffix', () => {
		expect(downloadExtensionFor('MESH', { url: '/api/media/gen-1/mesh', mesh_format: 'ply' })).toBe(
			'ply'
		);
	});

	it('falls back to the default mesh extension when neither is present', () => {
		expect(downloadExtensionFor('mesh', { url: '/api/media/gen-1/mesh' })).toBe('glb');
	});
});
