import { describe, expect, it } from 'vitest';
import type { GenerationHistoryItem } from '$lib/types/history';
import { inputRef, sourceOrigin } from './payload';
import { artifactRef, historyFileRef, libraryItemRef, pastedImage, recentHistoryPictures } from './sourceRefs';

function file(id: number, path: string, extra: Record<string, unknown> = {}) {
	return { id, file_path: path, file_type: 'image', is_final: true, created_at: '', ...extra };
}

describe('source refs', () => {
	it('builds a History ref from the generation file path with its id as a string', () => {
		const ref = historyFileRef({ id: 42, file_path: 'outputs/2026-11-01/gen-9/dusk.png', width: 640, height: 480 });
		expect(ref).toEqual({
			path: 'outputs/2026-11-01/gen-9/dusk.png',
			relative_path: 'outputs/2026-11-01/gen-9/dusk.png',
			url: '/api/media/generations/gen-9/dusk.png',
			name: 'dusk.png',
			type: 'image',
			generation_file_id: '42',
			width: 640,
			height: 480
		});
		expect(inputRef(ref)).toEqual({ generation_file_id: '42' });
		expect(sourceOrigin(ref)).toBe('history');
	});

	it('builds a Library ref with the item id, an uploads path and a name fallback', () => {
		const ref = libraryItemRef({ id: 'lib-1', filename: 'abc.png', original_filename: '  ', url: '/u/abc.png' });
		expect(ref).toEqual({ path: 'uploads/abc.png', relative_path: 'uploads/abc.png', url: '/u/abc.png', name: 'abc.png', type: 'image', item_id: 'lib-1' });
		expect(libraryItemRef({ id: 'lib-2', filename: 'x.png', original_filename: 'Dusk.png', url: '/u/x.png' }).name).toBe('Dusk.png');
		expect(sourceOrigin(ref)).toBe('library');
	});

	it('builds an upload ref that runs from the helper file', () => {
		const ref = artifactRef({ id: 'art-1', url: '/a/art-1.png', width: null, height: null }, 'drop.png');
		expect(ref).toEqual({ path: '/a/art-1.png', url: '/a/art-1.png', name: 'drop.png', type: 'image', artifact_id: 'art-1' });
		expect(inputRef(ref)).toEqual({ item_id: 'art-1' });
		expect(sourceOrigin(ref)).toBe('upload');
	});

	it('takes the newest final pictures from History up to the limit', () => {
		const generations = [
			{ files: [file(1, 'o/g1/a.png', { is_final: false }), file(2, 'o/g1/b.mp4', { file_type: 'video' }), file(3, 'o/g1/c.webp', { file_type: 'other' })] },
			{ files: [file(4, 'o/g2/d.png'), file(5, 'o/g2/e.png'), file(6, 'o/g2/f.png')] }
		] as unknown as GenerationHistoryItem[];
		expect(recentHistoryPictures(generations, 3).map((ref) => ref.generation_file_id)).toEqual(['3', '4', '5']);
	});

	it('finds the first pasted image file', () => {
		const picture = new File(['x'], 'p.png', { type: 'image/png' });
		const items = [
			{ kind: 'string', type: 'text/plain', getAsFile: () => null },
			{ kind: 'file', type: 'image/png', getAsFile: () => picture }
		] as unknown as DataTransferItemList;
		expect(pastedImage(items)).toBe(picture);
		expect(pastedImage(null)).toBeNull();
	});
});
