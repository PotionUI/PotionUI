import { describe, it, expect } from 'vitest';
import {
	acceptsValue,
	buildParams,
	fieldTypeFor,
	humanize,
	initialValues,
	isParamVisible,
	normalizeOptions,
	paramIssues,
	visibleParams
} from './params';
import type { ToolParam } from './types';

const select = (count: number, extra: Partial<ToolParam> = {}): ToolParam => ({
	name: 'pick',
	type: 'select',
	options: Array.from({ length: count }, (_, index) => ({ label: `Option ${index}`, value: `v${index}` })),
	...extra
});

describe('fieldTypeFor', () => {
	it('draws a select with four options or fewer as a segmented control', () => {
		expect(fieldTypeFor(select(2))).toBe('segmented');
		expect(fieldTypeFor(select(4))).toBe('segmented');
	});

	it('draws a select with five options or more as a dropdown', () => {
		expect(fieldTypeFor(select(5))).toBe('select');
		expect(fieldTypeFor(select(9))).toBe('select');
	});

	it('counts bare string options the same as labelled ones', () => {
		expect(fieldTypeFor({ name: 'fit', type: 'select', options: ['pad', 'cover'] })).toBe('segmented');
	});

	it('draws a number with both bounds as a slider and anything else as a number box', () => {
		expect(fieldTypeFor({ name: 'a', type: 'float', min: 0, max: 100 })).toBe('slider');
		expect(fieldTypeFor({ name: 'a', type: 'integer', min: 1, max: 8 })).toBe('slider');
		expect(fieldTypeFor({ name: 'a', type: 'integer' })).toBe('number_box');
		expect(fieldTypeFor({ name: 'a', type: 'float', min: 0 })).toBe('number_box');
		expect(fieldTypeFor({ name: 'a', type: 'float', max: 5 })).toBe('number_box');
	});

	it('does not offer a slider over an empty range', () => {
		expect(fieldTypeFor({ name: 'a', type: 'float', min: 3, max: 3 })).toBe('number_box');
	});

	it('maps the remaining types to their own controls', () => {
		expect(fieldTypeFor({ name: 'a', type: 'checkbox' })).toBe('checkbox');
		expect(fieldTypeFor({ name: 'a', type: 'color' })).toBe('color');
		expect(fieldTypeFor({ name: 'a', type: 'anchor' })).toBe('anchor');
		expect(fieldTypeFor({ name: 'a', type: 'string' })).toBe('string');
		expect(fieldTypeFor({ name: 'a', type: 'plugin_thing' })).toBe('string');
	});

	it('draws a select that asks for the anchor widget as the grid, whatever its option count', () => {
		expect(fieldTypeFor(select(9, { widget: 'anchor' }))).toBe('anchor');
	});
});

describe('normalizeOptions', () => {
	it('keeps labelled options and writes labels for bare ones', () => {
		const options = normalizeOptions({
			name: 'a',
			type: 'select',
			options: ['top_left', 90, { label: 'Custom', value: 'c' }]
		});
		expect(options).toEqual([
			{ label: 'Top left', value: 'top_left' },
			{ label: '90', value: 90 },
			{ label: 'Custom', value: 'c' }
		]);
	});
});

describe('humanize', () => {
	it('turns a param name into a label', () => {
		expect(humanize('keep_pitch')).toBe('Keep pitch');
		expect(humanize('bottom-right')).toBe('Bottom right');
		expect(humanize(undefined)).toBe('');
	});
});

describe('when', () => {
	const keyColor: ToolParam = { name: 'key_color', type: 'color', when: { field: 'key_mode', equals: 'color' } };
	const tolerance: ToolParam = { name: 'tolerance', type: 'float', min: 0, max: 100 };

	it('hides a param until its condition holds', () => {
		expect(isParamVisible(keyColor, { key_mode: 'auto' })).toBe(false);
		expect(isParamVisible(keyColor, { key_mode: 'color' })).toBe(true);
	});

	it('always shows a param without a condition', () => {
		expect(isParamVisible(tolerance, {})).toBe(true);
	});

	it('filters a list by the current values', () => {
		expect(visibleParams([keyColor, tolerance], { key_mode: 'auto' }).map((param) => param.name)).toEqual(['tolerance']);
		expect(visibleParams([keyColor, tolerance], { key_mode: 'color' }).map((param) => param.name)).toEqual([
			'key_color',
			'tolerance'
		]);
	});

	it('understands the list and logical forms of the preset grammar', () => {
		const both: ToolParam = {
			name: 'x',
			type: 'integer',
			when: [
				{ field: 'a', equals: 1 },
				{ field: 'b', equals: 2 }
			]
		};
		expect(isParamVisible(both, { a: 1, b: 2 })).toBe(true);
		expect(isParamVisible(both, { a: 1, b: 3 })).toBe(false);
		const either: ToolParam = {
			name: 'y',
			type: 'integer',
			when: { logic: 'OR', conditions: [{ field: 'a', equals: 1 }, { field: 'b', equals: 2 }] }
		};
		expect(isParamVisible(either, { a: 0, b: 2 })).toBe(true);
	});
});

describe('initialValues', () => {
	const params: ToolParam[] = [
		{ name: 'speed', type: 'float', min: 0.25, max: 4, default: 2 },
		{ name: 'keep_pitch', type: 'checkbox', default: true },
		{ name: 'mode', type: 'select', options: ['a', 'b', 'c'], default: 'a' },
		{ name: 'anchor', type: 'anchor' },
		{ name: 'width', type: 'integer' }
	];

	it('starts from the declared defaults', () => {
		expect(initialValues({ params, last_params: null })).toEqual({
			speed: 2,
			keep_pitch: true,
			mode: 'a',
			anchor: 'center',
			width: undefined
		});
	});

	it('starts a ranged number without a default on its minimum, which is what its slider shows', () => {
		const ranged: ToolParam[] = [
			{ name: 'repeats', type: 'integer', min: 1, max: 8 },
			{ name: 'feather', type: 'float', min: 0, max: 16 },
			{ name: 'offset', type: 'float', min: -5, max: 5 }
		];
		const values = initialValues({ params: ranged, last_params: null });
		expect(values).toEqual({ repeats: 1, feather: 0, offset: -5 });
		expect(buildParams(ranged, values)).toEqual({ repeats: 1, feather: 0, offset: -5 });
	});

	it('leaves a number with only one bound empty, since its box has nothing to show', () => {
		const half: ToolParam[] = [{ name: 'width', type: 'integer', min: 1 }, { name: 'cap', type: 'float', max: 9 }];
		expect(initialValues({ params: half, last_params: null })).toEqual({ width: undefined, cap: undefined });
	});

	it('still prefers a declared default and a remembered value over the minimum', () => {
		const withDefault: ToolParam[] = [{ name: 'repeats', type: 'integer', min: 1, max: 8, default: 3 }];
		expect(initialValues({ params: withDefault, last_params: null })).toEqual({ repeats: 3 });
		expect(initialValues({ params: withDefault, last_params: { repeats: 6 } })).toEqual({ repeats: 6 });
	});

	it('prefers what the user last used', () => {
		const values = initialValues({ params, last_params: { speed: 3.5, keep_pitch: false, mode: 'c', width: 640 } });
		expect(values).toMatchObject({ speed: 3.5, keep_pitch: false, mode: 'c', width: 640 });
	});

	it('falls back to the default for a remembered value that no longer fits', () => {
		const values = initialValues({ params, last_params: { speed: 9, mode: 'zzz', keep_pitch: 'yes' } });
		expect(values).toMatchObject({ speed: 2, mode: 'a', keep_pitch: true });
	});

	it('ignores remembered values for params the tool no longer has', () => {
		expect(Object.keys(initialValues({ params, last_params: { gone: 1 } }))).not.toContain('gone');
	});
});

describe('acceptsValue', () => {
	it('accepts transparent only where the color allows it', () => {
		const plain: ToolParam = { name: 'c', type: 'color' };
		const open: ToolParam = { name: 'c', type: 'color', allow_transparent: true };
		expect(acceptsValue(plain, 'transparent')).toBe(false);
		expect(acceptsValue(open, 'transparent')).toBe(true);
		expect(acceptsValue(open, '#00B140')).toBe(true);
		expect(acceptsValue(open, 'green')).toBe(false);
	});

	it('matches select values loosely so a numeric option survives a string round trip', () => {
		const degrees: ToolParam = { name: 'degrees', type: 'select', options: [90, 180, 270] };
		expect(acceptsValue(degrees, 90)).toBe(true);
		expect(acceptsValue(degrees, '180')).toBe(true);
		expect(acceptsValue(degrees, 45)).toBe(false);
	});
});

describe('paramIssues', () => {
	const params: ToolParam[] = [
		{ name: 'overlap_frames', type: 'integer', min: 0, max: 48, when: { field: 'overlap', equals: 'crossfade' } },
		{ name: 'width', type: 'integer', min: 1 }
	];

	it('flags a visible number outside its bounds', () => {
		expect(paramIssues(params, { overlap: 'crossfade', overlap_frames: 60 })).toEqual({ overlap_frames: 'At most 48' });
		expect(paramIssues(params, { width: 0 })).toEqual({ width: 'At least 1' });
	});

	it('does not check a hidden param', () => {
		expect(paramIssues(params, { overlap: 'cut', overlap_frames: 999 })).toEqual({});
	});

	it('leaves an empty optional number alone', () => {
		expect(paramIssues(params, { width: null })).toEqual({});
	});
});

describe('buildParams', () => {
	const params: ToolParam[] = [
		{ name: 'key_mode', type: 'select', options: ['auto', 'color'] },
		{ name: 'key_color', type: 'color', when: { field: 'key_mode', equals: 'color' } },
		{ name: 'width', type: 'integer' },
		{ name: 'height', type: 'integer' },
		{ name: 'feather', type: 'float' }
	];

	it('leaves out hidden params and empty ones', () => {
		expect(buildParams(params, { key_mode: 'auto', key_color: '#00B140', width: null, height: undefined, feather: '' })).toEqual({
			key_mode: 'auto'
		});
	});

	it('rounds integers and keeps floats as they are', () => {
		expect(buildParams(params, { key_mode: 'color', key_color: '#00B140', width: 640.4, feather: 1.5 })).toEqual({
			key_mode: 'color',
			key_color: '#00B140',
			width: 640,
			feather: 1.5
		});
	});

	it('keeps an explicit zero and an explicit false', () => {
		const withSwitch: ToolParam[] = [{ name: 'despill', type: 'checkbox' }, { name: 'feather', type: 'float' }];
		expect(buildParams(withSwitch, { despill: false, feather: 0 })).toEqual({ despill: false, feather: 0 });
	});
});
