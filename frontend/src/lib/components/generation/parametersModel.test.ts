import { describe, expect, it } from 'vitest';
import {
	FOLD_ABOVE,
	FOLD_SHOW,
	buildParameterView,
	classifyParameter,
	foldShortRows,
	humanizeKey,
	orderKeys,
	rawValue,
	type ParameterRow
} from './parametersModel';

const keys = (rows: ParameterRow[]) => rows.map((r) => r.key);
const build = (params: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
	buildParameterView({ params, ...extra });

describe('humanizeKey', () => {
	it('makes unknown keys readable', () => {
		expect(humanizeKey('magic number')).toBe('Magic number');
		expect(humanizeKey('clipLength')).toBe('Clip length');
		expect(humanizeKey('grain.seed_lock')).toBe('Grain seed lock');
		expect(humanizeKey('upscale-factor')).toBe('Upscale factor');
	});
});

describe('hiding', () => {
	it.each([
		['null', null],
		['undefined', undefined],
		['empty string', ''],
		['whitespace', '  \n '],
		['empty list', []],
		['empty object', {}]
	])('hides %s', (_name, value) => {
		expect(classifyParameter('x', value)).toBeNull();
	});

	it('keeps zero and false', () => {
		expect(classifyParameter('x', 0)?.display).toBe('0');
		expect(classifyParameter('x', false)?.display).toBe('Off');
	});

	it('hides a value equal to a model name or file name, whatever the key', () => {
		const models = [{ name: 'Krea-2 Photoreal', filename: 'krea2_photoreal_v3.safetensors' }];
		const v = build(
			{ whatever: 'Krea-2 Photoreal', other: 'KREA2_PHOTOREAL_V3.safetensors', third: 'krea2_photoreal_v3', kept: 'euler' },
			{ models }
		);
		expect(keys(v.shorts)).toEqual(['kept']);
	});

	it('matches a model file by its base name when the path is stored', () => {
		const v = build({ a: 'm.safetensors', b: 'euler' }, { models: [{ filename: 'checkpoints/sub/m.safetensors' }] });
		expect(keys(v.shorts)).toEqual(['b']);
	});

	it('does not hide anything without models', () => {
		expect(keys(build({ a: 'Krea-2 Photoreal' }).shorts)).toEqual(['a']);
	});
});

describe('long text versus short rows', () => {
	it('keeps a prompt-like key with a short value as a row', () => {
		const v = build({ prompter: 'a red bicycle', negative_prompt: 'blurry' });
		expect(v.longs).toHaveLength(0);
		expect(keys(v.shorts)).toEqual(['prompter', 'negative_prompt']);
	});

	it('turns any key with 200 characters into a block', () => {
		const v = build({ zzz: 'x'.repeat(200), prompt: 'short' });
		expect(keys(v.longs)).toEqual(['zzz']);
		expect(keys(v.shorts)).toEqual(['prompt']);
	});

	it('treats exactly 80 characters as short and 81 as long', () => {
		expect(classifyParameter('k', 'a'.repeat(80))?.kind).toBe('short');
		expect(classifyParameter('k', 'a'.repeat(81))?.kind).toBe('long');
	});

	it('treats a line break under 80 characters as long', () => {
		const row = classifyParameter('k', 'one\ntwo');
		expect(row?.kind).toBe('long');
		expect(row?.text).toBe('one\ntwo');
	});

	it.each(['prompt', 'textarea', 'markdown', 'TEXTAREA'])('treats declared %s with a short value as long', (type) => {
		expect(classifyParameter('k', 'hi', { k: { type } })?.kind).toBe('long');
	});

	it('does not treat other declared types as long', () => {
		expect(classifyParameter('k', 'hi', { k: { type: 'select' } })?.kind).toBe('short');
	});

	it('keeps two long texts in field order', () => {
		const long = 'y'.repeat(120);
		const v = build({ b: long, a: long, n: 1 }, { order: ['a', 'n', 'b'] });
		expect(keys(v.longs)).toEqual(['a', 'b']);
		expect(keys(v.shorts)).toEqual(['n']);
	});

	it('has no long block when there is no long text', () => {
		expect(build({ a: 1, b: 'x' }).longs).toEqual([]);
	});

	it('makes a long JSON value a mono block and a short one a compact mono row', () => {
		const big = { items: Array.from({ length: 30 }, (_, i) => i) };
		const block = classifyParameter('o', big);
		expect(block?.kind).toBe('long');
		expect(block?.mono).toBe(true);
		expect(block?.raw).toBe(JSON.stringify(big));
		const small = classifyParameter('o', { move: 'dolly', speed: 0.4 });
		expect(small?.kind).toBe('short');
		expect(small?.display).toBe('{"move":"dolly","speed":0.4}');
		expect(small?.mono).toBe(true);
	});
});

describe('formatting', () => {
	it('formats real booleans, boolean strings and declared checkboxes', () => {
		expect(classifyParameter('k', true)).toMatchObject({ display: 'On', off: false });
		expect(classifyParameter('k', false)).toMatchObject({ display: 'Off', off: true });
		expect(classifyParameter('k', 'true')).toMatchObject({ display: 'On', off: false });
		expect(classifyParameter('k', 'False')).toMatchObject({ display: 'Off', off: true });
		expect(classifyParameter('k', 1, { k: { type: 'checkbox' } })).toMatchObject({ display: 'On' });
		expect(classifyParameter('k', 0, { k: { type: 'checkbox' } })).toMatchObject({ display: 'Off' });
	});

	it('keeps numbers raw, tabular, at most four decimals', () => {
		expect(classifyParameter('seed', 1117566997)).toMatchObject({ display: '1117566997', mono: true });
		expect(classifyParameter('seed', 48213907765)?.display).toBe('48213907765');
		expect(classifyParameter('k', 4.5)?.display).toBe('4.5');
		expect(classifyParameter('k', 0.123456)?.display).toBe('0.1235');
		expect(classifyParameter('k', '8')).toMatchObject({ display: '8', mono: true });
		expect(classifyParameter('k', '0.35')?.display).toBe('0.35');
		expect(classifyParameter('k', '12345678901234567890')?.display).toBe('12345678901234567890');
	});

	it('formats WxH strings', () => {
		expect(classifyParameter('k', '768x1344')).toMatchObject({ display: '768 × 1344', mono: true });
		expect(classifyParameter('k', '1216×832')?.display).toBe('1216 × 832');
		expect(classifyParameter('k', '768 x 1344')?.display).toBe('768 × 1344');
		expect(classifyParameter('k', 'axb')?.display).toBe('axb');
	});

	it('shows other text in sans', () => {
		expect(classifyParameter('k', 'euler')).toMatchObject({ display: 'euler', mono: false });
	});

	it('appends a unit only when declared', () => {
		expect(classifyParameter('d', 4)).toMatchObject({ unit: '' });
		expect(classifyParameter('d', 4, { d: { label: 'Length' } })).toMatchObject({ unit: '' });
		expect(classifyParameter('duration', 4, { duration: { unit: 's' } })).toMatchObject({ unit: 's', unitSpaced: true });
		expect(classifyParameter('p', 50, { p: { unit: '%' } })).toMatchObject({ unit: '%', unitSpaced: false });
		expect(classifyParameter('p', 1.5, { p: { unit: '×' } })).toMatchObject({ unitSpaced: false });
		expect(classifyParameter('p', 15, { p: { unit: '°' } })).toMatchObject({ unitSpaced: false });
	});

	it('uses the declared label else the humanized key', () => {
		expect(classifyParameter('positive_prompt', 'x', { positive_prompt: { label: 'Prompt' } })?.label).toBe('Prompt');
		expect(classifyParameter('clipLength', 3)?.label).toBe('Clip length');
	});

	it('copies the raw value', () => {
		expect(classifyParameter('k', '768x1344')?.raw).toBe('768x1344');
		expect(classifyParameter('k', 'true')?.raw).toBe('true');
		expect(classifyParameter('k', 0.123456)?.raw).toBe('0.123456');
		expect(rawValue({ a: 1 })).toBe('{"a":1}');
	});
});

describe('order', () => {
	const params = { b: 1, a: 2, c: 3 };

	it('follows the response order without parameter_order', () => {
		expect(orderKeys(params)).toEqual(['b', 'a', 'c']);
		expect(orderKeys(params, [])).toEqual(['b', 'a', 'c']);
		expect(orderKeys(params, null)).toEqual(['b', 'a', 'c']);
	});

	it('follows parameter_order then appends leftovers, skipping unknown names and duplicates', () => {
		expect(orderKeys(params, ['c', 'zzz', 'a', 'c'])).toEqual(['c', 'a', 'b']);
	});

	it('is not sorted by name', () => {
		expect(keys(build({ z: 1, a: 2 }).shorts)).toEqual(['z', 'a']);
	});
});

describe('an old generation with no meta and no order', () => {
	it('renders every key with the humanized label, no units, response order', () => {
		const v = build({ steps: 24, enhance: false, resolution: '768x1344', positive_prompt: 'p'.repeat(100) });
		expect(keys(v.longs)).toEqual(['positive_prompt']);
		expect(v.shorts.map((r) => [r.label, r.display, r.unit])).toEqual([
			['Steps', '24', ''],
			['Enhance', 'Off', ''],
			['Resolution', '768 × 1344', '']
		]);
	});

	it('tolerates null params', () => {
		expect(buildParameterView({ params: null })).toEqual({ longs: [], shorts: [] });
	});
});

describe('fold', () => {
	const rows = (n: number): ParameterRow[] =>
		Array.from({ length: n }, (_, i) => classifyParameter(`k${i}`, i + 1) as ParameterRow);

	it('uses the agreed thresholds', () => {
		expect(FOLD_ABOVE).toBe(10);
		expect(FOLD_SHOW).toBe(8);
	});

	it.each([0, 4, 8, 9, 10])('never folds %i short values', (n) => {
		const f = foldShortRows(rows(n), false);
		expect(f).toMatchObject({ foldable: false, hidden: 0, total: n });
		expect(f.visible).toHaveLength(n);
	});

	it('folds 11 to 8 and hides 3', () => {
		const f = foldShortRows(rows(11), false);
		expect(f).toMatchObject({ foldable: true, hidden: 3, total: 11 });
		expect(keys(f.visible)).toEqual(keys(rows(8)));
	});

	it('folds 23 short values (24 params, one long) and counts only the short ones', () => {
		const params: Record<string, unknown> = { brief: 'b'.repeat(120) };
		for (let i = 0; i < 23; i++) params[`p${i}`] = i;
		const view = buildParameterView({ params });
		expect(view.longs).toHaveLength(1);
		const f = foldShortRows(view.shorts, false);
		expect(f).toMatchObject({ foldable: true, total: 23, hidden: 15 });
		expect(keys(f.visible)).toEqual(['p0', 'p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7']);
	});

	it('shows everything when expanded', () => {
		const f = foldShortRows(rows(24), true);
		expect(f).toMatchObject({ foldable: true, hidden: 0, total: 24 });
		expect(f.visible).toHaveLength(24);
	});

	it('shows the first 8 in field order, not response order', () => {
		const params: Record<string, unknown> = {};
		for (let i = 0; i < 12; i++) params[`p${i}`] = i;
		const order = Array.from({ length: 12 }, (_, i) => `p${11 - i}`);
		const view = buildParameterView({ params, order });
		expect(keys(foldShortRows(view.shorts, false).visible)).toEqual(order.slice(0, 8));
	});
});
