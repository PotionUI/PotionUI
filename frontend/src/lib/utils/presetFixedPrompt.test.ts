import { describe, it, expect } from 'vitest';
import {
	buildFixedPromptPayload,
	directorModeNames,
	fixedPromptDirtyFields,
	fixedPromptDraftFrom,
	fixedPromptForMode,
	fixedPromptPieces,
	normalizeFixedPrompt,
	validateFixedPromptDraft
} from './presetFixedPrompt';

const SAVED = { prefix: 'film still,', suffix: 'grain', negative: 'text', modes: null };

describe('fixed prompt draft', () => {
	it('round-trips a saved value', () => {
		expect(buildFixedPromptPayload(fixedPromptDraftFrom(SAVED))).toEqual(SAVED);
		expect(fixedPromptDirtyFields(fixedPromptDraftFrom(SAVED), SAVED)).toEqual([]);
	});

	it('marks each changed field', () => {
		const draft = { ...fixedPromptDraftFrom(SAVED), suffix: 'grain, 35mm', limitModes: true, modes: ['txt2img'] };
		expect(fixedPromptDirtyFields(draft, SAVED)).toEqual(['suffix', 'modes']);
	});

	it('ignores the order of the mode list when comparing', () => {
		const saved = { ...SAVED, modes: ['a', 'b'] };
		expect(fixedPromptDirtyFields({ ...fixedPromptDraftFrom(saved), modes: ['b', 'a'] }, saved)).toEqual([]);
	});

	it('sends null modes unless limited, and limited modes in mode order', () => {
		expect(buildFixedPromptPayload({ ...fixedPromptDraftFrom(SAVED), modes: ['edit'] }).modes).toBeNull();
		expect(
			buildFixedPromptPayload({ ...fixedPromptDraftFrom(SAVED), limitModes: true, modes: ['edit', 'txt2img'] }, ['txt2img', 'edit'])
				.modes
		).toEqual(['txt2img', 'edit']);
	});

	it('refuses text over 2000 characters and an empty mode list', () => {
		const errors = validateFixedPromptDraft({
			prefix: 'x'.repeat(2001),
			suffix: 'x'.repeat(2000),
			negative: '',
			limitModes: true,
			modes: []
		});
		expect(Object.keys(errors).sort()).toEqual(['modes', 'prefix']);
	});

	it('normalizes a partial API body', () => {
		expect(normalizeFixedPrompt({ prefix: 'a' })).toEqual({ prefix: 'a', suffix: '', negative: '', modes: null });
	});
});

describe('fixedPromptForMode', () => {
	it('is null when there is nothing to add', () => {
		expect(fixedPromptForMode({ prefix: ' ', suffix: '', negative: '', modes: null }, 'txt2img')).toBeNull();
		expect(fixedPromptForMode(null, 'txt2img')).toBeNull();
	});

	it('follows the mode allow-list', () => {
		const fixed = { ...SAVED, modes: ['txt2img'] };
		expect(fixedPromptForMode(fixed, 'txt2img')).toEqual({ prefix: 'film still,', suffix: 'grain', negative: 'text' });
		expect(fixedPromptForMode(fixed, 'edit')).toBeNull();
	});

	it('is never applied in a director mode', () => {
		expect(fixedPromptForMode(SAVED, 'video', { directorActive: true })).toBeNull();
	});
});

describe('fixedPromptPieces', () => {
	const parts = { prefix: 'film still,', suffix: 'grain', negative: 'text' };

	it('wraps the user prompt with single spaces like the server', () => {
		const pieces = fixedPromptPieces('a cat', parts, 'prompt');
		expect(pieces.map((piece) => piece.text).join('')).toBe('film still, a cat grain');
		expect(pieces.filter((piece) => !piece.fixed).map((piece) => piece.text)).toEqual(['a cat']);
	});

	it('joins the negative addition with a comma', () => {
		const pieces = fixedPromptPieces('blurry', parts, 'negative');
		expect(pieces.map((piece) => piece.text).join('')).toBe('blurry, text');
		expect(pieces.at(-1)).toEqual({ text: 'text', fixed: true });
	});

	it('skips empty parts', () => {
		expect(fixedPromptPieces('', parts, 'prompt').map((piece) => piece.text).join('')).toBe('film still, grain');
		expect(fixedPromptPieces('a cat', null, 'prompt')).toEqual([{ text: 'a cat', fixed: false }]);
	});
});

describe('directorModeNames', () => {
	it('lists the modes that run the Video or Music Director', () => {
		const vars = {
			video_director: { modes: { t2v: {} }, preset_modes: ['video'] },
			music_director: { modes: { song: {} }, preset_modes: ['music'] }
		};
		expect(directorModeNames(vars, ['txt2img', 'video', 'music'])).toEqual(['video', 'music']);
	});

	it('is empty without vars', () => {
		expect(directorModeNames(null, ['txt2img'])).toEqual([]);
	});
});
