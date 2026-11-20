import { describe, expect, it } from 'vitest';
import { EDIT_SOURCE_NAME_MAX } from '$lib/services/api/edits';
import { editSourceOf } from './editLink';

describe('editSourceOf', () => {
	it('sends the picture name with the source', () => {
		expect(editSourceOf({ path: 'p', name: 'dusk.png', generation_file_id: '5' })).toEqual({ kind: 'history', id: '5', name: 'dusk.png' });
		expect(editSourceOf({ path: 'p', name: ' logo.png ', item_id: 'i1' })).toEqual({ kind: 'library', id: 'i1', name: 'logo.png' });
		expect(editSourceOf({ path: 'p', name: 'scan.jpg', artifact_id: 'a1' })).toEqual({ kind: 'upload', id: 'a1', name: 'scan.jpg' });
	});

	it('leaves the name out when the picture has none', () => {
		expect(editSourceOf({ path: 'p', item_id: 'i1' })).toEqual({ kind: 'library', id: 'i1' });
		expect(editSourceOf({ path: 'p', name: '   ', item_id: 'i1' })).toEqual({ kind: 'library', id: 'i1' });
		expect(editSourceOf(null)).toBeNull();
		expect(editSourceOf({ path: 'p', name: 'x.png' })).toBeNull();
	});

	it('cuts a long name to the length the server accepts', () => {
		const source = editSourceOf({ path: 'p', name: 'a'.repeat(300), item_id: 'i1' });
		expect(source?.name).toHaveLength(EDIT_SOURCE_NAME_MAX);
	});
});
