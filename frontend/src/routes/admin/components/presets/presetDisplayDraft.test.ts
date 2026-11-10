import { describe, it, expect } from 'vitest';
import type { PresetAdminDetail } from '$lib/types/api';
import {
	buildDisplayPayload,
	buildModesPayload,
	discardDisplayEditor,
	displayEditorDirtyCount,
	displayEditorFailed,
	displayEditorFor,
	displayEditorSaved,
	displayDirtyKeys,
	displayDraftFrom,
	modesDirtyCount,
	modesDraftFrom,
	validateDisplayDraft,
	validateModesDraft,
	withOfferedToggled
} from './presetDisplayDraft';

const AVAILABLE = [
	{ name: 'txt2img', label: 'Text to Image' },
	{ name: 'img2img', label: 'Image to Image' },
	{ name: 'edit', label: 'Edit' }
];

function detail(overrides: Partial<PresetAdminDetail> = {}): PresetAdminDetail {
	return {
		preset_id: 'krea2',
		preset_db_id: '01JD7',
		is_mirror: false,
		source_preset_id: null,
		source_name: null,
		mirror_state: null,
		file: { name: 'Krea-2', description: 'From the file', cover: 'public/cover.webp' },
		display: { name: null, description: null, cover: null },
		effective: { name: 'Krea-2', description: 'From the file', cover: 'public/cover.webp' },
		modes: { available: AVAILABLE, offered: null, first: null },
		mirrors: [],
		assignment_count: 0,
		group_count: 0,
		...overrides
	};
}

describe('display draft', () => {
	it('starts empty when nothing is overridden', () => {
		expect(displayDraftFrom(detail())).toEqual({ name: '', description: '' });
	});

	it('marks only the fields that changed', () => {
		const saved = detail({ display: { name: 'Realistic', description: null, cover: null } });
		expect(displayDirtyKeys({ name: 'Realistic', description: '' }, saved)).toEqual([]);
		expect(displayDirtyKeys({ name: '  Realistic  ', description: '' }, saved)).toEqual([]);
		expect(displayDirtyKeys({ name: 'Film Covers', description: '' }, saved)).toEqual(['name']);
		expect(displayDirtyKeys({ name: 'Realistic', description: 'New' }, saved)).toEqual(['description']);
	});

	it('clears a field back to the file value with null', () => {
		const payload = buildDisplayPayload({ name: '   ', description: '' }, 'display/01.webp');
		expect(payload).toEqual({ name: null, description: null, cover: 'display/01.webp' });
	});

	it('trims the name it sends', () => {
		expect(buildDisplayPayload({ name: '  Anime ', description: 'x' }, null).name).toBe('Anime');
	});

	it('requires a name only for a mirror', () => {
		expect(validateDisplayDraft({ name: '', description: '' }, false)).toEqual({});
		expect(validateDisplayDraft({ name: ' ', description: '' }, true).name).toBe('A mirror needs a name.');
	});

	it('limits the name to 80 and the description to 4000 characters', () => {
		expect(validateDisplayDraft({ name: 'x'.repeat(80), description: 'y'.repeat(4000) }, true)).toEqual({});
		const errors = validateDisplayDraft({ name: 'x'.repeat(81), description: 'y'.repeat(4001) }, false);
		expect(errors.name).toBeTruthy();
		expect(errors.description).toBeTruthy();
	});
});

describe('mirror modes draft', () => {
	it('offers every source mode when the mirror has no list', () => {
		expect(modesDraftFrom(detail().modes)).toEqual({ offered: ['txt2img', 'img2img', 'edit'], first: 'txt2img' });
	});

	it('keeps the saved first mode and drops modes the source no longer has', () => {
		const modes = { available: AVAILABLE, offered: ['edit', 'gone', 'txt2img'], first: 'edit' };
		expect(modesDraftFrom(modes)).toEqual({ offered: ['txt2img', 'edit'], first: 'edit' });
	});

	it('moves the first mode when it is unticked', () => {
		const next = withOfferedToggled({ offered: ['txt2img', 'edit'], first: 'edit' }, ['txt2img'], AVAILABLE);
		expect(next).toEqual({ offered: ['txt2img'], first: 'txt2img' });
	});

	it('counts the offered list and the first mode as separate changes', () => {
		const modes = detail().modes;
		expect(modesDirtyCount({ offered: ['txt2img', 'img2img', 'edit'], first: 'txt2img' }, modes)).toBe(0);
		expect(modesDirtyCount({ offered: ['txt2img', 'edit'], first: 'txt2img' }, modes)).toBe(1);
		expect(modesDirtyCount({ offered: ['txt2img', 'edit'], first: 'edit' }, modes)).toBe(2);
	});

	it('refuses an empty list or a first mode that is not offered', () => {
		expect(validateModesDraft({ offered: [], first: '' })).toBeTruthy();
		expect(validateModesDraft({ offered: ['txt2img'], first: 'edit' })).toBeTruthy();
		expect(validateModesDraft({ offered: ['txt2img'], first: 'txt2img' })).toBeNull();
	});

	it('sends null for every mode and the source order otherwise', () => {
		expect(buildModesPayload({ offered: ['edit', 'txt2img', 'img2img'], first: 'edit' }, AVAILABLE)).toEqual({
			offered: null,
			first: 'edit'
		});
		expect(buildModesPayload({ offered: ['edit', 'txt2img'], first: 'edit' }, AVAILABLE)).toEqual({
			offered: ['txt2img', 'edit'],
			first: 'edit'
		});
	});
});

describe('display editor', () => {
	const realistic = detail({ display: { name: 'Realistic', description: 'Photos', cover: null } });
	const mirror = detail({
		preset_id: '01MIRROR',
		is_mirror: true,
		source_preset_id: 'krea2',
		mirror_state: 'ok',
		display: { name: 'Film Covers', description: null, cover: 'display/01.webp' },
		modes: { available: AVAILABLE, offered: ['txt2img', 'edit'], first: 'edit' }
	});

	function edited() {
		const editor = displayEditorFor(realistic);
		return { ...editor, draft: { name: 'Studio', description: 'Photos' }, attempted: true, serverErrors: { name: 'Taken' } };
	}

	it('starts clean on the loaded values', () => {
		const editor = displayEditorFor(realistic);
		expect(editor.draft).toEqual({ name: 'Realistic', description: 'Photos' });
		expect(displayEditorDirtyCount(editor)).toBe(0);
	});

	it('resets to the other preset’s values with nothing unsaved when the preset changes', () => {
		expect(displayEditorDirtyCount(edited())).toBe(1);
		const next = displayEditorFor(mirror);
		expect(next.detail.preset_id).toBe('01MIRROR');
		expect(next.draft).toEqual({ name: 'Film Covers', description: '' });
		expect(next.modes).toEqual({ offered: ['txt2img', 'edit'], first: 'edit' });
		expect(displayEditorDirtyCount(next)).toBe(0);
		expect(next.serverErrors).toEqual({});
		expect(next.attempted).toBe(false);
	});

	it('counts mirror mode changes as unsaved', () => {
		const editor = displayEditorFor(mirror);
		expect(displayEditorDirtyCount({ ...editor, modes: { offered: ['txt2img', 'edit'], first: 'txt2img' } })).toBe(1);
	});

	it('restores the loaded values and clears errors on Discard', () => {
		const discarded = discardDisplayEditor(edited());
		expect(discarded.draft).toEqual({ name: 'Realistic', description: 'Photos' });
		expect(discarded.serverErrors).toEqual({});
		expect(discarded.attempted).toBe(false);
		expect(displayEditorDirtyCount(discarded)).toBe(0);
	});

	it('keeps the draft and shows the error next to its field when a save fails', () => {
		const result = displayEditorFailed(edited(), {
			code: 'invalid_display',
			message: 'Invalid display.',
			errors: ['name: must be 80 characters or fewer']
		});
		expect(result.editor.draft).toEqual({ name: 'Studio', description: 'Photos' });
		expect(result.editor.serverErrors.name).toBe('Must be 80 characters or fewer');
		expect(displayEditorDirtyCount(result.editor)).toBe(1);
		expect(result.toast).toBeNull();
	});

	it('keeps the draft and raises a toast when a failed save names no field', () => {
		const result = displayEditorFailed(edited(), { code: 'preset_not_installed', message: 'Install it first.', errors: [] });
		expect(result.editor.draft.name).toBe('Studio');
		expect(result.toast).toBe('Install it first.');
	});

	it('puts a modes refusal under the modes field', () => {
		const editor = displayEditorFor(mirror);
		const result = displayEditorFailed(editor, { code: 'invalid_modes', message: 'Bad modes.', errors: ['first: not offered'] });
		expect(result.editor.serverErrors.modes).toBe('Not offered');
		expect(result.editor.modes).toEqual(editor.modes);
	});

	it('adopts the saved values after a save', () => {
		const saved = displayEditorSaved(edited(), { ...realistic, display: { name: 'Studio', description: 'Photos', cover: null } });
		expect(saved.draft.name).toBe('Studio');
		expect(displayEditorDirtyCount(saved)).toBe(0);
		expect(saved.serverErrors).toEqual({});
	});
});
