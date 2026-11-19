import { describe, expect, it } from 'vitest';
import { mediaFileStem, nameStem } from './sources';
import { mediaRefForItem } from './shortcuts';
import { editSourceOf } from './image/editLink';
import { historyFileRef } from './image/sourceRefs';
import { resolveClipSourceRef } from './video/videoEdit';

describe('media names for tabs and edits', () => {
	it('treats a bare number as no name and keeps the stem of a real filename', () => {
		expect(nameStem('1.mp4')).toBeNull();
		expect(nameStem('0.png')).toBeNull();
		expect(nameStem('rain.mp4')).toBe('rain');
		expect(nameStem('A cat. On a roof')).toBe('A cat. On a roof');
	});

	it('names the tab from the title, then a real filename, never a bare number', () => {
		expect(mediaFileStem({ path: 'o/g/1.mp4', name: '1.mp4', title: 'a lighthouse at dusk' })).toBe('a lighthouse at dusk');
		expect(mediaFileStem({ path: 'o/g/1.mp4', name: '1.mp4' })).toBeNull();
		expect(mediaFileStem({ path: 'o/g/1.mp4' })).toBeNull();
		expect(mediaFileStem({ path: 'uploads/dock.mp4', name: 'dock.mp4' })).toBe('dock');
	});

	it('carries the prompt excerpt from a History picture into its ref', () => {
		const file = { id: 3, file_path: 'outputs/d/gen-1/0.png', width: 8, height: 8 };
		expect(historyFileRef(file, { form_data: { prompt: 'a red fox' } })).toMatchObject({ name: '0.png', title: 'a red fox' });
		expect(historyFileRef(file)).not.toHaveProperty('title');
	});

	it('carries the title through the Edit shortcut and into the saved edit name', () => {
		const ref = mediaRefForItem({ id: 'g:0', kind: 'video', url: '/u', filename: '1.mp4', title: 'a red fox', generationFileId: '9' });
		expect(ref.title).toBe('a red fox');
		expect(editSourceOf(ref)).toEqual({ kind: 'history', id: '9', name: 'a red fox' });
		expect(editSourceOf({ path: 'p', name: '1.mp4', generation_file_id: '9' })).toEqual({ kind: 'history', id: '9' });
	});

	it('reopens a saved History clip under its stored name, and a stored bare number gives no title', async () => {
		const port = { getLibraryItem: async () => ({ success: true, data: undefined }) } as never;
		expect(await resolveClipSourceRef({ kind: 'history', id: '4', name: 'a red fox' }, port)).toMatchObject({ name: 'a red fox', title: 'a red fox' });
		const legacy = await resolveClipSourceRef({ kind: 'history', id: '4', name: '1' }, port);
		expect(legacy).not.toHaveProperty('title');
		expect(mediaFileStem(legacy)).toBeNull();
	});
});
