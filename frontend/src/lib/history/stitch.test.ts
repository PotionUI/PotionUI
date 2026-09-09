import { describe, it, expect } from 'vitest';
import {
	DEFAULT_STITCH_OPTIONS,
	collectParamKeys,
	computeLayout,
	defaultParamKeys,
	drawStitch,
	paramLinesFor,
	stitchFileName,
	type ParamLine,
	type StitchDrawTarget,
	type StitchItem,
	type StitchOptions
} from './stitch';

function item(overrides: Partial<StitchItem> = {}): StitchItem {
	return {
		id: 'a',
		width: 1024,
		height: 1024,
		parameters: {},
		prompt: '',
		...overrides
	};
}

function options(overrides: Partial<StitchOptions> = {}): StitchOptions {
	return { ...DEFAULT_STITCH_OPTIONS, ...overrides };
}

describe('collectParamKeys', () => {
	it('unions keys in first-seen order and appends the synthetic ones', () => {
		const keys = collectParamKeys([
			item({ id: 'a', parameters: { seed: 1, sampler: 'euler' } }),
			item({ id: 'b', parameters: { steps: 30, seed: 2 }, prompt: 'a cat' })
		]);

		expect(keys.map((k) => k.key)).toEqual(['seed', 'sampler', 'steps', 'resolution', 'prompt']);
		expect(keys.filter((k) => k.synthetic).map((k) => k.key)).toEqual(['resolution', 'prompt']);
	});

	it('drops keys no image has a value for', () => {
		const keys = collectParamKeys([
			item({ parameters: { seed: 1, lora: '', scheduler: null } })
		]);

		expect(keys.map((k) => k.key)).toEqual(['seed', 'resolution']);
	});

	it('omits a synthetic key when a real parameter already owns the name', () => {
		const keys = collectParamKeys([item({ parameters: { resolution: '512x512' } })]);

		expect(keys).toEqual([{ key: 'resolution', label: 'resolution', synthetic: false }]);
	});

	it('offers no resolution when the file has no dimensions', () => {
		const keys = collectParamKeys([item({ width: 0, height: 0, parameters: { seed: 1 } })]);

		expect(keys.map((k) => k.key)).toEqual(['seed']);
	});
});

describe('defaultParamKeys', () => {
	it('keeps only the well-known keys that the selection actually offers', () => {
		const available = collectParamKeys([
			item({ parameters: { seed: 1, steps: 30, guidance_rescale: 0.7, sampler: 'euler' } })
		]);

		expect(defaultParamKeys(available)).toEqual(['seed', 'steps', 'sampler', 'resolution']);
	});
});

describe('paramLinesFor', () => {
	it('skips the keys this image lacks', () => {
		const lines = paramLinesFor(item({ parameters: { seed: 7, sampler: '  ' } }), [
			'seed',
			'sampler',
			'steps'
		]);

		expect(lines).toEqual([{ key: 'seed', label: 'seed', value: '7' }]);
	});

	it('formats numbers plainly and models by basename', () => {
		const lines = paramLinesFor(
			item({
				parameters: {
					cfg_scale: 7.5,
					steps: 30,
					model: '/models/checkpoints/sdxl_base.safetensors'
				}
			}),
			['cfg_scale', 'steps', 'model']
		);

		expect(lines).toEqual([
			{ key: 'cfg_scale', label: 'cfg scale', value: '7.5' },
			{ key: 'steps', label: 'steps', value: '30' },
			{ key: 'model', label: 'model', value: 'sdxl_base' }
		]);
	});

	it('derives the synthetic resolution and prompt from the item', () => {
		const lines = paramLinesFor(item({ width: 1216, height: 832, prompt: '  a fox  ' }), [
			'resolution',
			'prompt'
		]);

		expect(lines).toEqual([
			{ key: 'resolution', label: 'resolution', value: '1216×832' },
			{ key: 'prompt', label: 'prompt', value: 'a fox' }
		]);
	});
});

describe('computeLayout', () => {
	const mixed = [
		{ width: 100, height: 50, lines: 2 },
		{ width: 60, height: 80, lines: 0 }
	];

	it('lays a row out left to right with the gap as gutter and padding', () => {
		const layout = computeLayout(mixed, options({ layout: 'row', gap: 10, tileMaxSide: 'original' }));

		expect(layout.columns).toBe(2);
		expect(layout.rows).toBe(1);
		expect({ width: layout.width, height: layout.height }).toEqual({ width: 190, height: 142 });
		expect(layout.tiles[0].image).toEqual({ x: 10, y: 40, width: 100, height: 50 });
		expect(layout.tiles[1].image).toEqual({ x: 120, y: 10, width: 60, height: 80 });
	});

	it('sizes each label box from that image line count', () => {
		const layout = computeLayout(mixed, options({ layout: 'row', gap: 10, tileMaxSide: 'original' }));

		expect(layout.tiles[0].label).toEqual({ x: 10, y: 90, width: 100, height: 42 });
		expect(layout.tiles[1].label.height).toBe(0);
		expect(layout.tiles[0].fontSize).toBe(12);
	});

	it('drops every label box when parameters are hidden', () => {
		const layout = computeLayout(
			mixed,
			options({ layout: 'row', gap: 10, tileMaxSide: 'original', showParams: false })
		);

		expect(layout.tiles[0].label.height).toBe(0);
		expect(layout.height).toBe(100);
	});

	it('stacks a column into one lane', () => {
		const layout = computeLayout(mixed, options({ layout: 'column', gap: 10, tileMaxSide: 'original' }));

		expect(layout.columns).toBe(1);
		expect(layout.rows).toBe(2);
		expect({ width: layout.width, height: layout.height }).toEqual({ width: 120, height: 202 });
		expect(layout.tiles[1].image).toEqual({ x: 30, y: 112, width: 60, height: 80 });
	});

	it('wraps a grid at the requested column count', () => {
		const square = { width: 100, height: 100, lines: 0 };
		const layout = computeLayout(
			[square, square, square],
			options({ layout: 'grid', columns: 2, gap: 10, tileMaxSide: 'original' })
		);

		expect({ columns: layout.columns, rows: layout.rows }).toEqual({ columns: 2, rows: 2 });
		expect({ width: layout.width, height: layout.height }).toEqual({ width: 230, height: 230 });
		expect(layout.tiles[2].image).toEqual({ x: 10, y: 120, width: 100, height: 100 });
	});

	it('scales tiles down to the requested max side without upscaling', () => {
		const layout = computeLayout(
			[
				{ width: 2048, height: 1024, lines: 0 },
				{ width: 200, height: 200, lines: 0 }
			],
			options({ layout: 'row', gap: 0, tileMaxSide: 1024 })
		);

		expect(layout.tiles[0].image).toMatchObject({ width: 1024, height: 512 });
		expect(layout.tiles[1].image).toMatchObject({ width: 200, height: 200 });
	});

	it('steps the tile side down and reports the clamp when the canvas is too wide', () => {
		const huge = { width: 12000, height: 12000, lines: 0 };
		const layout = computeLayout([huge, huge], options({ layout: 'row', gap: 0, tileMaxSide: 'original' }));

		expect(layout.clamped).toBe(true);
		expect(layout.tileMaxSide).toBe(1024);
		expect(layout.width).toBe(2048);
	});

	it('steps down again when the pixel budget rather than a side is blown', () => {
		const square = { width: 1024, height: 1024, lines: 0 };
		const layout = computeLayout(
			new Array(100).fill(square),
			options({ layout: 'grid', columns: 10, gap: 0, tileMaxSide: 1024 })
		);

		expect(layout.width).toBeLessThanOrEqual(16384);
		expect(layout.clamped).toBe(true);
		expect(layout.tileMaxSide).toBe(512);
		expect(layout.width * layout.height).toBeLessThanOrEqual(80_000_000);
	});

	it('leaves a composition that fits unclamped', () => {
		const layout = computeLayout(
			[{ width: 100, height: 100, lines: 0 }],
			options({ layout: 'row', gap: 0, tileMaxSide: 'original' })
		);

		expect(layout.clamped).toBe(false);
		expect(layout.tileMaxSide).toBe('original');
	});
});

interface Recorded {
	fills: Array<{ style: unknown; x: number; y: number; width: number; height: number }>;
	images: Array<{ image: unknown; x: number; y: number; width: number; height: number }>;
	texts: Array<{ text: string; x: number; y: number; maxWidth?: number; font: string; style: unknown }>;
	cleared: Array<{ x: number; y: number; width: number; height: number }>;
}

function fakeContext(): StitchDrawTarget & { recorded: Recorded } {
	const recorded: Recorded = { fills: [], images: [], texts: [], cleared: [] };
	return {
		recorded,
		fillStyle: '',
		font: '',
		textBaseline: 'alphabetic' as CanvasTextBaseline,
		clearRect(x, y, width, height) {
			recorded.cleared.push({ x, y, width, height });
		},
		fillRect(x, y, width, height) {
			recorded.fills.push({ style: this.fillStyle, x, y, width, height });
		},
		drawImage(image, x, y, width, height) {
			recorded.images.push({ image, x, y, width, height });
		},
		fillText(text, x, y, maxWidth) {
			recorded.texts.push({ text, x, y, maxWidth, font: this.font, style: this.fillStyle });
		}
	};
}

describe('drawStitch', () => {
	const tiles = [
		{ width: 100, height: 50, lines: 2 },
		{ width: 60, height: 80, lines: 0 }
	];
	const lines: ParamLine[][] = [
		[
			{ key: 'seed', label: 'seed', value: '7' },
			{ key: 'steps', label: 'steps', value: '30' }
		],
		[]
	];
	const images = [{ id: 'left' }, { id: 'right' }] as unknown as CanvasImageSource[];

	it('paints the background, the tiles and the parameter lines', () => {
		const opts = options({ layout: 'row', gap: 10, tileMaxSide: 'original', background: 'dark' });
		const layout = computeLayout(tiles, opts);
		const ctx = fakeContext();

		drawStitch(ctx, images, layout, lines, opts);

		expect(ctx.recorded.fills).toEqual([
			{ style: '#0f0f11', x: 0, y: 0, width: 190, height: 142 }
		]);
		expect(ctx.recorded.images).toEqual([
			{ image: images[0], x: 10, y: 40, width: 100, height: 50 },
			{ image: images[1], x: 120, y: 10, width: 60, height: 80 }
		]);
		expect(ctx.recorded.texts).toEqual([
			{ text: 'seed 7', x: 15, y: 95, maxWidth: 90, font: ctx.font, style: '#e7e7ea' },
			{ text: 'steps 30', x: 15, y: 111, maxWidth: 90, font: ctx.font, style: '#e7e7ea' }
		]);
		expect(ctx.font).toBe('12px ui-monospace, SFMono-Regular, Menlo, monospace');
	});

	it('clears instead of filling on a transparent background', () => {
		const opts = options({ layout: 'row', gap: 10, tileMaxSide: 'original', background: 'transparent' });
		const layout = computeLayout(tiles, opts);
		const ctx = fakeContext();

		drawStitch(ctx, images, layout, lines, opts);

		expect(ctx.recorded.fills).toEqual([]);
		expect(ctx.recorded.cleared).toEqual([{ x: 0, y: 0, width: 190, height: 142 }]);
	});

	it('skips an image that failed to load and draws no text when parameters are hidden', () => {
		const opts = options({
			layout: 'row',
			gap: 10,
			tileMaxSide: 'original',
			background: 'light',
			showParams: false
		});
		const layout = computeLayout(tiles, opts);
		const ctx = fakeContext();

		drawStitch(ctx, [null, images[1]], layout, lines, opts);

		expect(ctx.recorded.images).toEqual([
			{ image: images[1], x: 120, y: 10, width: 60, height: 80 }
		]);
		expect(ctx.recorded.texts).toEqual([]);
		expect(ctx.recorded.fills[0].style).toBe('#f6f6f7');
	});
});

describe('stitchFileName', () => {
	it('stamps the local date and minute', () => {
		expect(stitchFileName(new Date(2026, 8, 9, 4, 7))).toBe('stitch-20260909-0407.png');
	});
});
