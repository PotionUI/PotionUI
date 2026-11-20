import { describe, it, expect } from 'vitest';
import { isOptionalPromptMode, isPromptlessMode } from './promptlessMode';

describe('isPromptlessMode', () => {
	it('returns true when the mode is listed in promptless_modes', () => {
		const vars = { promptless_modes: ['upscale', 'video_upscale'] };
		expect(isPromptlessMode(vars, 'upscale')).toBe(true);
		expect(isPromptlessMode(vars, 'video_upscale')).toBe(true);
	});

	it('returns false for a mode not in the list', () => {
		const vars = { promptless_modes: ['upscale'] };
		expect(isPromptlessMode(vars, 't2v')).toBe(false);
	});

	it('returns false when the var is absent', () => {
		expect(isPromptlessMode({ num_prompts: 2 }, 'upscale')).toBe(false);
		expect(isPromptlessMode({}, 'upscale')).toBe(false);
	});

	it('returns false when vars is null/undefined', () => {
		expect(isPromptlessMode(null, 'upscale')).toBe(false);
		expect(isPromptlessMode(undefined, 'upscale')).toBe(false);
	});

	it('returns false when mode is null/undefined/empty', () => {
		const vars = { promptless_modes: ['upscale'] };
		expect(isPromptlessMode(vars, null)).toBe(false);
		expect(isPromptlessMode(vars, undefined)).toBe(false);
		expect(isPromptlessMode(vars, '')).toBe(false);
	});

	it('returns false when promptless_modes is not an array', () => {
		expect(isPromptlessMode({ promptless_modes: 'upscale' }, 'upscale')).toBe(false);
		expect(isPromptlessMode({ promptless_modes: true }, 'upscale')).toBe(false);
	});

	describe('a mode that is promptless only for some form values', () => {
		const vars = {
			promptless_modes: [
				'upscale',
				{
					mode: 'control',
					when: {
						logic: 'AND',
						conditions: [
							{ field: 'guide_only', equals: true },
							{ field: 'guide', not_in: ['none', 'grayscale'] }
						]
					}
				}
			]
		};

		it('needs the condition to hold', () => {
			expect(isPromptlessMode(vars, 'control', { guide_only: true, guide: 'openpose' })).toBe(true);
			expect(isPromptlessMode(vars, 'control', { guide_only: false, guide: 'openpose' })).toBe(false);
			expect(isPromptlessMode(vars, 'control', { guide_only: true, guide: 'none' })).toBe(false);
		});

		it('is never promptless without the form', () => {
			expect(isPromptlessMode(vars, 'control')).toBe(false);
		});

		it('keeps plain mode names working beside it', () => {
			expect(isPromptlessMode(vars, 'upscale')).toBe(true);
			expect(isPromptlessMode(vars, 'txt2img', { guide_only: true, guide: 'canny' })).toBe(false);
		});
	});

	it('skips an entry without a mode', () => {
		expect(isPromptlessMode({ promptless_modes: [{ when: { field: 'x', equals: 1 } }] }, 'control', { x: 1 })).toBe(false);
	});
});

describe('isOptionalPromptMode', () => {
	it('returns true when the mode is listed in optional_prompt_modes', () => {
		const vars = { optional_prompt_modes: ['recast', 'talk'] };
		expect(isOptionalPromptMode(vars, 'recast')).toBe(true);
		expect(isOptionalPromptMode(vars, 'talk')).toBe(true);
	});

	it('returns false for a mode not in the list', () => {
		expect(isOptionalPromptMode({ optional_prompt_modes: ['recast'] }, 't2v')).toBe(false);
	});

	it('does not read promptless_modes', () => {
		expect(isOptionalPromptMode({ promptless_modes: ['upscale'] }, 'upscale')).toBe(false);
		expect(isPromptlessMode({ optional_prompt_modes: ['recast'] }, 'recast')).toBe(false);
	});

	it('returns false when the var is absent, not an array, or the inputs are empty', () => {
		expect(isOptionalPromptMode({}, 'recast')).toBe(false);
		expect(isOptionalPromptMode(null, 'recast')).toBe(false);
		expect(isOptionalPromptMode(undefined, 'recast')).toBe(false);
		expect(isOptionalPromptMode({ optional_prompt_modes: 'recast' }, 'recast')).toBe(false);
		expect(isOptionalPromptMode({ optional_prompt_modes: ['recast'] }, null)).toBe(false);
		expect(isOptionalPromptMode({ optional_prompt_modes: ['recast'] }, '')).toBe(false);
	});

	describe('a mode that is optional only for some form values', () => {
		const vars = {
			optional_prompt_modes: [
				'recast',
				{ mode: 'restyle', when: { field: 'restyle_task', in: ['colourise', 'restore'] } }
			]
		};

		it('needs the condition to hold', () => {
			expect(isOptionalPromptMode(vars, 'restyle', { restyle_task: 'colourise' })).toBe(true);
			expect(isOptionalPromptMode(vars, 'restyle', { restyle_task: 'restore' })).toBe(true);
			expect(isOptionalPromptMode(vars, 'restyle', { restyle_task: 'custom' })).toBe(false);
		});

		it('is never optional without the form', () => {
			expect(isOptionalPromptMode(vars, 'restyle')).toBe(false);
		});

		it('keeps plain mode names working beside it', () => {
			expect(isOptionalPromptMode(vars, 'recast')).toBe(true);
			expect(isOptionalPromptMode(vars, 'recast', { restyle_task: 'custom' })).toBe(true);
		});

		it('skips an entry without a mode', () => {
			expect(isOptionalPromptMode({ optional_prompt_modes: [{ when: { field: 'x', equals: 1 } }] }, 'restyle', { x: 1 })).toBe(false);
		});
	});

	describe('a mode listed in both vars', () => {
		it('is promptless, not optional', () => {
			const vars = { promptless_modes: ['upscale'], optional_prompt_modes: ['upscale', 'recast'] };
			expect(isPromptlessMode(vars, 'upscale')).toBe(true);
			expect(isOptionalPromptMode(vars, 'upscale')).toBe(false);
			expect(isOptionalPromptMode(vars, 'recast')).toBe(true);
		});

		it('turns optional again while the promptless condition does not hold', () => {
			const vars = {
				promptless_modes: [{ mode: 'control', when: { field: 'guide_only', equals: true } }],
				optional_prompt_modes: ['control']
			};
			expect(isOptionalPromptMode(vars, 'control', { guide_only: true })).toBe(false);
			expect(isOptionalPromptMode(vars, 'control', { guide_only: false })).toBe(true);
		});
	});
});

describe('a song mode that needs lyrics unless it is instrumental', () => {
	const vars = { optional_prompt_modes: [{ mode: 'song', when: { field: 'instrumental', equals: true } }] };

	it('makes the prompt optional with the toggle on', () => {
		expect(isOptionalPromptMode(vars, 'song', { instrumental: true })).toBe(true);
	});

	it('keeps the prompt required with the toggle off or unset', () => {
		expect(isOptionalPromptMode(vars, 'song', { instrumental: false })).toBe(false);
		expect(isOptionalPromptMode(vars, 'song', {})).toBe(false);
	});
});
