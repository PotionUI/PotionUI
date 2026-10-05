import { describe, it, expect } from 'vitest';
import {
	isNegativeInert,
	resolveNegativeAppliesWhen,
	type NegativePromptDeclarations
} from './negativeApplied';

const krea: NegativePromptDeclarations = {
	applies_when: null,
	modes: {
		txt2img: {
			default: {
				logic: 'OR',
				conditions: [
					{ field: 'cfg', greater_than: 1 },
					{
						logic: 'AND',
						conditions: [
							{ field: 'nag_enabled', equals: true },
							{ field: 'nag_scale', greater_than: 1 }
						]
					}
				]
			},
			variants: {}
		}
	}
};

describe('isNegativeInert', () => {
	it('never hides the negative without a declaration', () => {
		expect(isNegativeInert(null, 'txt2img', null, { cfg: 1 })).toBe(false);
		expect(isNegativeInert(undefined, 'txt2img', null, { cfg: 1 })).toBe(false);
		expect(isNegativeInert({ applies_when: null, modes: {} }, 'txt2img', null, { cfg: 1 })).toBe(false);
	});

	it('never hides the negative without form data', () => {
		expect(isNegativeInert(krea, 'txt2img', null, null)).toBe(false);
	});

	it('reads greater_than against the resolved form values', () => {
		expect(isNegativeInert(krea, 'txt2img', null, { cfg: 1, nag_enabled: false, nag_scale: 1 })).toBe(true);
		expect(isNegativeInert(krea, 'txt2img', null, { cfg: 4, nag_enabled: false, nag_scale: 1 })).toBe(false);
	});

	it('applies when any branch of an OR holds and all of an AND hold', () => {
		expect(isNegativeInert(krea, 'txt2img', null, { cfg: 1, nag_enabled: true, nag_scale: 1.5 })).toBe(false);
		expect(isNegativeInert(krea, 'txt2img', null, { cfg: 1, nag_enabled: true, nag_scale: 1 })).toBe(true);
		expect(isNegativeInert(krea, 'txt2img', null, { cfg: 1, nag_enabled: false, nag_scale: 1.5 })).toBe(true);
	});

	it('treats a list as AND', () => {
		const decl: NegativePromptDeclarations = {
			applies_when: [
				{ field: 'cfg', greater_than: 1 },
				{ field: 'sampler', not_equals: 'lcm' }
			]
		};
		expect(isNegativeInert(decl, 'any', null, { cfg: 3, sampler: 'euler' })).toBe(false);
		expect(isNegativeInert(decl, 'any', null, { cfg: 3, sampler: 'lcm' })).toBe(true);
	});

	it('reads a literal false as never applied and true as always applied', () => {
		expect(isNegativeInert({ applies_when: false }, 'upscale', null, null)).toBe(true);
		expect(isNegativeInert({ applies_when: false }, 'upscale', null, { cfg: 7 })).toBe(true);
		expect(isNegativeInert({ applies_when: true }, 'upscale', null, { cfg: 1 })).toBe(false);
		expect(
			isNegativeInert({ modes: { upscale: { default: false, variants: {} } } }, 'upscale', null, {})
		).toBe(true);
	});

	it('reads equals strictly', () => {
		const decl: NegativePromptDeclarations = { applies_when: { field: 'nag_enabled', equals: true } };
		expect(isNegativeInert(decl, 'm', null, { nag_enabled: true })).toBe(false);
		expect(isNegativeInert(decl, 'm', null, { nag_enabled: 'true' })).toBe(true);
	});
});

describe('resolveNegativeAppliesWhen', () => {
	const preset = { field: 'cfg', greater_than: 1 };
	const own = { field: 'nag_scale', greater_than: 1 };
	const decl: NegativePromptDeclarations = {
		applies_when: preset,
		modes: { video: { default: own, variants: { fast: preset, slow: own } } }
	};

	it('uses the selected variant, then the mode default, then the preset level', () => {
		expect(resolveNegativeAppliesWhen(decl, 'video', 'fast')).toBe(preset);
		expect(resolveNegativeAppliesWhen(decl, 'video', 'slow')).toBe(own);
		expect(resolveNegativeAppliesWhen(decl, 'video', null)).toBe(own);
		expect(resolveNegativeAppliesWhen(decl, 'video', 'unknown')).toBe(own);
		expect(resolveNegativeAppliesWhen(decl, 'upscale', null)).toBe(preset);
	});
});
