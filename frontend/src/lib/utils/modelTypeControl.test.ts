import { describe, it, expect } from 'vitest';
import {
	AUTOMATIC_TYPE,
	typeChangeFromDraft,
	typeOptions,
	typePackagingLine,
	typeSourceIsAssertion,
	typeSourceLine,
	type ModelTypeInfo
} from './modelTypeControl';

function info(overrides: Partial<ModelTypeInfo> = {}): ModelTypeInfo {
	return {
		source: 'folder',
		folder_type: 'checkpoint',
		family: null,
		variant: null,
		classifier: null,
		components: [],
		verdict_status: null,
		packaging: null,
		...overrides
	};
}

describe('typeSourceLine', () => {
	it('names the family and variant for a header verdict', () => {
		expect(typeSourceLine(info({ source: 'header', family: 'flux', variant: 'flux1' }))).toBe(
			'Detected from the file: Flux (flux1)'
		);
	});

	it('omits the variant when there is none', () => {
		expect(typeSourceLine(info({ source: 'header', family: 'qwen_image' }))).toBe(
			'Detected from the file: Qwen Image'
		);
	});

	it('falls back when a header verdict has no family', () => {
		expect(typeSourceLine(info({ source: 'header' }))).toBe('Detected from the file');
	});

	it('upper-cases acronym families', () => {
		expect(typeSourceLine(info({ source: 'header', family: 'sdxl', variant: 'base' }))).toBe(
			'Detected from the file: SDXL (base)'
		);
	});

	it('maps each assertion source', () => {
		expect(typeSourceLine(info({ source: 'admin' }))).toBe('Set by an admin');
		expect(typeSourceLine(info({ source: 'recipe' }))).toBe('Set by a recipe');
		expect(typeSourceLine(info({ source: 'download' }))).toBe('Set when it was downloaded');
	});

	it('treats folder and unknown sources as the folder type', () => {
		expect(typeSourceLine(info())).toBe('From the folder it is in');
		expect(typeSourceLine(info({ source: 'whatever' }))).toBe('From the folder it is in');
	});

	it('explains an undefined model whatever the source says', () => {
		const line = "The file header didn't match any known model. Choose its type.";
		expect(typeSourceLine(info({ source: 'header', verdict_status: 'undecided' }), 'undefined')).toBe(line);
		expect(typeSourceLine(info({ source: 'folder' }), 'undefined')).toBe(line);
		expect(typeSourceLine(null, 'undefined')).toBe(line);
	});

	it('is empty without type info', () => {
		expect(typeSourceLine(null)).toBe('');
		expect(typeSourceLine(undefined)).toBe('');
	});
});

describe('typeSourceIsAssertion', () => {
	it('is true only for admin, recipe and download', () => {
		expect(typeSourceIsAssertion(info({ source: 'admin' }))).toBe(true);
		expect(typeSourceIsAssertion(info({ source: 'recipe' }))).toBe(true);
		expect(typeSourceIsAssertion(info({ source: 'download' }))).toBe(true);
		expect(typeSourceIsAssertion(info({ source: 'header' }))).toBe(false);
		expect(typeSourceIsAssertion(info())).toBe(false);
		expect(typeSourceIsAssertion(null)).toBe(false);
	});
});

describe('typePackagingLine', () => {
	it('explains a full checkpoint used by diffusion pickers', () => {
		expect(typePackagingLine(info({ packaging: 'full_checkpoint', family: 'flux' }))).toBe(
			'Full checkpoint, diffusion model used by Flux pickers'
		);
	});

	it('is empty for other packaging', () => {
		expect(typePackagingLine(info())).toBe('');
		expect(typePackagingLine(null)).toBe('');
	});
});

describe('typeChangeFromDraft', () => {
	it('is no change when the draft equals the current type', () => {
		expect(typeChangeFromDraft('lora', 'lora')).toEqual({ kind: 'none' });
	});

	it('sets a different type', () => {
		expect(typeChangeFromDraft('diffusion_model', 'checkpoint')).toEqual({
			kind: 'set',
			modelType: 'diffusion_model'
		});
	});

	it('resets to automatic', () => {
		expect(typeChangeFromDraft(AUTOMATIC_TYPE, 'checkpoint')).toEqual({ kind: 'reset' });
	});
});

describe('typeOptions', () => {
	it('lists assignable types without undefined or llm', () => {
		const values = typeOptions('checkpoint').map((o) => o.value);
		expect(values).toContain('diffusion_model');
		expect(values).not.toContain('undefined');
		expect(values).not.toContain('llm');
	});

	it('prepends a disabled current option for a type that cannot be assigned', () => {
		const options = typeOptions('undefined');
		expect(options[0]).toEqual({ value: 'undefined', label: 'Needs a type', disabled: true });
	});
});
