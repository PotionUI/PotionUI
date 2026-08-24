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
});
