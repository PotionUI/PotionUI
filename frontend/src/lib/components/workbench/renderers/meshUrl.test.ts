import { describe, it, expect } from 'vitest';
import { resolveMeshUrl, resolveMeshMetadata, resolveMeshFormat } from './meshUrl';

describe('resolveMeshUrl', () => {
	it('prefers current_mesh, the live-generation field', () => {
		expect(
			resolveMeshUrl({ current_mesh: '/api/mesh/a.glb', url: '/api/mesh/b.glb' })
		).toBe('/api/mesh/a.glb');
	});

	it('falls back to originalUrl over url for a gallery-item shape', () => {
		expect(
			resolveMeshUrl({ originalUrl: '/api/mesh/orig.glb', url: '/api/mesh/thumb.glb' })
		).toBe('/api/mesh/orig.glb');
	});

	it('falls back to url when nothing else is present', () => {
		expect(resolveMeshUrl({ url: '/api/mesh/only.glb' })).toBe('/api/mesh/only.glb');
	});

	it('returns null when the file has no resolvable URL', () => {
		expect(resolveMeshUrl({})).toBeNull();
	});

	it('returns null for a nullish file', () => {
		expect(resolveMeshUrl(null)).toBeNull();
		expect(resolveMeshUrl(undefined)).toBeNull();
	});
});

describe('resolveMeshMetadata', () => {
	it('reads nested mesh_metadata for the live-generation shape', () => {
		expect(
			resolveMeshMetadata({
				current_mesh: '/api/mesh/a.glb',
				mesh_metadata: { vertex_count: 120, face_count: 40, filename: 'a.glb', format: 'glb' }
			})
		).toEqual({ vertexCount: 120, faceCount: 40, filename: 'a.glb', format: 'glb' });
	});

	it('reads flat fields for a gallery/history-item shape', () => {
		expect(
			resolveMeshMetadata({
				url: '/api/mesh/b.glb',
				vertex_count: 8,
				face_count: 2,
				mesh_name: 'b.glb',
				mesh_format: 'glb'
			})
		).toEqual({ vertexCount: 8, faceCount: 2, filename: 'b.glb', format: 'glb' });
	});

	it('prefers nested mesh_metadata over flat fields when both are present', () => {
		expect(
			resolveMeshMetadata({
				mesh_metadata: { vertex_count: 1 },
				vertex_count: 999
			}).vertexCount
		).toBe(1);
	});

	it('returns all-null metadata for a file with none', () => {
		expect(resolveMeshMetadata({ url: '/api/mesh/c.glb' })).toEqual({
			vertexCount: null,
			faceCount: null,
			filename: null,
			format: null
		});
	});

	it('returns all-null metadata for a nullish file', () => {
		expect(resolveMeshMetadata(null)).toEqual({
			vertexCount: null,
			faceCount: null,
			filename: null,
			format: null
		});
	});
});

describe('resolveMeshFormat', () => {
	it('surfaces a real non-glb format from the flat gallery/history shape', () => {
		expect(resolveMeshFormat({ url: '/api/mesh/b.ply', mesh_format: 'ply' })).toBe('ply');
	});

	it('surfaces a real non-glb format from the nested live-generation shape', () => {
		expect(
			resolveMeshFormat({ current_mesh: '/api/mesh/a.obj', mesh_metadata: { format: 'obj' } })
		).toBe('obj');
	});

	it('lowercases an uppercase server-sent format', () => {
		expect(resolveMeshFormat({ mesh_format: 'PLY' })).toBe('ply');
	});

	it('falls back to glb only when the server sent no format at all', () => {
		expect(resolveMeshFormat({ url: '/api/mesh/c.glb' })).toBe('glb');
		expect(resolveMeshFormat(null)).toBe('glb');
	});
});
