import { describe, expect, it } from 'vitest';
import {
	SAME_NAME_MESSAGE,
	editTweak,
	isChanged,
	loadedTweak,
	runValues,
	saveAsNewBody,
	saveToBody,
	savedTweak,
	sameSettings,
	sessionSaveMessage,
	startTweak,
	validateSetupName
} from './tweaks';

const httpError = (status: number, data: unknown) => ({ response: { status, data } });

describe('sameSettings', () => {
	it('ignores key order', () => {
		expect(sameSettings({ a: 1, b: { x: 1, y: 2 } }, { b: { y: 2, x: 1 }, a: 1 }, {})).toBe(true);
	});

	it('treats a missing value as the default', () => {
		expect(sameSettings({ steps: 28 }, {}, { steps: 28 })).toBe(true);
		expect(sameSettings({ steps: 20 }, {}, { steps: 28 })).toBe(false);
	});

	it('compares lists in order', () => {
		expect(sameSettings({ loras: [{ id: 'a' }, { id: 'b' }] }, { loras: [{ id: 'b' }, { id: 'a' }] }, {})).toBe(false);
		expect(sameSettings({ loras: [{ id: 'a' }] }, { loras: [{ id: 'a' }] }, {})).toBe(true);
	});
});

describe('a tweak', () => {
	const fresh = () => startTweak('k', { steps: 28 }, { steps: 28 });

	it('is not changed until the form has reported, and takes its first report as the baseline', () => {
		expect(isChanged(fresh())).toBe(false);
		const first = editTweak(fresh(), { steps: 28, extra: 'filled by the form' });
		expect(first.settled).toBe(true);
		expect(isChanged(first)).toBe(false);
	});

	it('is changed by an edit and clears when the value goes back', () => {
		const first = editTweak(fresh(), { steps: 28 });
		const edited = editTweak(first, { steps: 12 });
		expect(isChanged(edited)).toBe(true);
		expect(isChanged(editTweak(edited, { steps: 28 }))).toBe(false);
	});

	it('clears once saved', () => {
		const edited = editTweak(editTweak(fresh(), { steps: 28 }), { steps: 12 });
		expect(isChanged(savedTweak(edited))).toBe(false);
		expect(savedTweak(edited).base).toEqual({ steps: 12 });
	});

	it('hands the run the form values only while they differ', () => {
		const base = { steps: 28 };
		const edited = editTweak(editTweak(fresh(), { steps: 28 }), { steps: 12, resolution: '2048' });
		expect(runValues(base, edited, 'k')).toEqual({ steps: 12, resolution: '2048' });
		expect(runValues(base, edited, 'other')).toBe(base);
		expect(runValues(base, undefined, 'k')).toBe(base);
		expect(runValues(base, editTweak(fresh(), { steps: 28 }), 'k')).toBe(base);
	});
});

describe('save bodies', () => {
	const target = { mode: 'editor:edit' };

	it('updates the session with the values keyed by the editor key, with its version', () => {
		expect(saveToBody({ name: 'Soft portrait', version: 4, target, values: { steps: 12 } })).toEqual({
			name: 'Soft portrait',
			data: { 'editor:edit': { formData: { steps: 12 } } },
			mode: 'editor:edit',
			base_version: 4,
			kind: 'editor'
		});
	});

	it('creates an editor session for the preset and mode', () => {
		expect(saveAsNewBody({ presetId: 'qwen', name: 'Mine', target: { mode: 'editor:edit' }, values: { steps: 12 } })).toEqual({
			preset_id: 'qwen',
			name: 'Mine',
			data: { 'editor:edit': { formData: { steps: 12 } } },
			mode: 'editor:edit',
			kind: 'editor'
		});
	});
});

describe('a loaded version', () => {
	it('marks the tweak changed against the saved values', () => {
		const settled = editTweak(startTweak('k', {}, { steps: 28 }), { steps: 28 });
		const loaded = loadedTweak(settled, { steps: 12 });
		expect(isChanged(loaded)).toBe(true);
		expect(loaded.base).toEqual({ steps: 28 });
		expect(isChanged(savedTweak(loaded))).toBe(false);
	});
});

describe('names', () => {
	it('needs a name, refuses a repeat in any case and a long one', () => {
		expect(validateSetupName('  ', [])).toBe('Enter a name for the setup.');
		expect(validateSetupName('soft PORTRAIT', ['Soft portrait'])).toBe(SAME_NAME_MESSAGE);
		expect(validateSetupName('x'.repeat(81), [])).toBe('Use a shorter name.');
		expect(validateSetupName(' Fresh ', ['Soft portrait'])).toBeNull();
	});
});

describe('save failures in plain words', () => {
	it('names the Generate page when the name belongs to one of its sessions', () => {
		expect(sessionSaveMessage(httpError(409, { detail: { error: 'session_kind_mismatch' } }))).toBe(
			'That name is already used by one of your sessions on the Generate page. Choose another name.'
		);
	});

	it('explains a version conflict, the three 422s, a lost connection and a server fault', () => {
		expect(sessionSaveMessage(httpError(409, { detail: { error: 'session_version_conflict' } }))).toContain('changed somewhere else');
		expect(sessionSaveMessage(httpError(422, { detail: { error: 'session_mode_required' } }))).toContain('no model mode');
		expect(sessionSaveMessage(httpError(422, { detail: { error: 'session_mode_unknown' } }))).toContain('no longer has that mode');
		expect(sessionSaveMessage(httpError(422, { detail: { error: 'session_mode_not_offered' } }))).toContain('no longer offers this tool');
		expect(sessionSaveMessage(new Error('offline'))).toBe('The connection was lost. Try again.');
		expect(sessionSaveMessage(httpError(500, {}))).toBe('Something went wrong on the server. Try again.');
	});

	it('uses the server message for anything else', () => {
		expect(sessionSaveMessage(httpError(400, { detail: { error: 'odd', message: 'Name is too long' } }))).toBe('Name is too long');
	});
});
