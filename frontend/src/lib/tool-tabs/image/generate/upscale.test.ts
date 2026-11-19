import { describe, expect, it } from 'vitest';
import { MAX_OUTPUT_SIDE, isResizedParams, isResizingTask, markResizingTasks, pictureKey, readRunOptions, resizedPictureEntry, resizedPictureOf, scaleText, targetFromHeight, targetFromWidth, upscaleSize } from './upscale';

const done = (over: Record<string, unknown> = {}) => ({
	task: 'upscale',
	run: { id: 'er_1', status: 'done', error: null },
	chosen: 0,
	results: [{ patch: { item_id: 'p1' }, url: '/p1.png', seed: 1, size: { width: 4096, height: 3072 } }],
	...over
});

describe('upscaleSize', () => {
	const source = { width: 1024, height: 768 };

	it('multiplies by a factor and rounds', () => {
		expect(upscaleSize(source, { scale: 2 })).toEqual({ ok: true, size: { width: 2048, height: 1536 } });
		expect(upscaleSize({ width: 1001, height: 333 }, { scale: 1.5 })).toEqual({ ok: true, size: { width: 1502, height: 500 } });
	});

	it('fits a target inside keeping the shape', () => {
		expect(upscaleSize(source, { target: { width: 3000, height: 3000 } })).toEqual({ ok: true, size: { width: 3000, height: 2250 } });
		expect(upscaleSize(source, { target: { width: 4096, height: 3072 } })).toEqual({ ok: true, size: { width: 4096, height: 3072 } });
	});

	it('refuses a size that is not larger, an unusable one and an oversized one', () => {
		expect(upscaleSize(source, { scale: 1 })).toEqual({ ok: false, reason: 'not_larger' });
		expect(upscaleSize(source, { target: { width: 800, height: 600 } })).toEqual({ ok: false, reason: 'not_larger' });
		expect(upscaleSize(source, { scale: 0 })).toEqual({ ok: false, reason: 'invalid' });
		expect(upscaleSize(source, {})).toEqual({ ok: false, reason: 'invalid' });
		expect(upscaleSize(source, { scale: MAX_OUTPUT_SIDE })).toEqual({ ok: false, reason: 'too_large' });
		expect(upscaleSize({ width: 9000, height: 9000 }, { scale: 2 })).toEqual({ ok: false, reason: 'too_large' });
	});
});

describe('targets that keep the shape', () => {
	it('follows the other side from the picture shape', () => {
		const source = { width: 1000, height: 500 };
		expect(targetFromWidth(source, 3000)).toEqual({ width: 3000, height: 1500 });
		expect(targetFromHeight(source, 1500)).toEqual({ width: 3000, height: 1500 });
		expect(targetFromWidth(source, 0)).toEqual({ width: 1, height: 1 });
	});
});

describe('run options', () => {
	it('reads exactly one of scale or target', () => {
		expect(readRunOptions({ scale: 4, target: { width: 10, height: 10 } })).toEqual({ scale: 4 });
		expect(readRunOptions({ target: { width: 100.4, height: 50 } })).toEqual({ target: { width: 100, height: 50 } });
		expect(readRunOptions({ scale: -1 })).toBeNull();
		expect(readRunOptions(null)).toBeNull();
	});

	it('describes them for the step row', () => {
		expect(scaleText({ scale: 2 })).toBe('2×');
		expect(scaleText({ target: { width: 10, height: 10 } })).toBe('Custom size');
		expect(scaleText(undefined)).toBe('');
	});
});

describe('resizing tasks', () => {
	it('knows upscale and any task the server marks as resizing', () => {
		expect(isResizingTask('upscale')).toBe(true);
		expect(isResizingTask('edit_region')).toBe(false);
		markResizingTasks(['super_res']);
		expect(isResizingTask('super_res')).toBe(true);
	});
});

describe('resized pictures', () => {
	it('is a landed picture only when the run is done and the result has a size', () => {
		expect(resizedPictureOf(done())).toEqual({ size: { width: 4096, height: 3072 }, patch: 'p1' });
		expect(resizedPictureOf(done({ run: { id: 'er_1', status: 'running', error: null } }))).toBeNull();
		expect(resizedPictureOf(done({ results: [{ patch: { item_id: 'p1' }, url: '/p1.png', seed: 1 }] }))).toBeNull();
		expect(resizedPictureEntry(done())).toEqual(['upscale', { width: 4096, height: 3072, patch: 'p1' }]);
	});

	it('marks a step as resized by its task even before it lands', () => {
		expect(isResizedParams(done({ results: [], run: { id: 'x', status: 'running', error: null } }))).toBe(true);
		expect(isResizedParams({ task: 'edit_region', run: { status: 'done' }, results: [] })).toBe(false);
	});
});

describe('pictureKey', () => {
	const step = (tool: string, params: unknown, extra: Record<string, unknown> = {}) => ({ tool, params, hidden: false, ...extra });

	it('changes with any visible step beneath and ignores hidden ones, slices and runs in flight', () => {
		const base = [step('crop', { a: 1 }), step('paint', { layer: 'l1' })];
		const key = pictureKey(base);
		expect(pictureKey([...base, step('effects', { name: 'x' }, { hidden: true })])).toBe(key);
		expect(pictureKey([...base, step('slice', {})])).toBe(key);
		expect(pictureKey([...base, step('generate', { run: { status: 'running' } })])).toBe(key);
		expect(pictureKey([step('crop', { a: 2 }), base[1]])).not.toBe(key);
		expect(pictureKey([...base, step('generate', { run: { status: 'done' } })])).not.toBe(key);
		expect(pictureKey([base[0], step('paint', { layer: 'l1' }, { layer: { opacity: 0.5 } })])).not.toBe(key);
	});
});
