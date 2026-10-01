import { describe, it, expect } from 'vitest';
import { isPromptlessMode } from './promptlessMode';

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
