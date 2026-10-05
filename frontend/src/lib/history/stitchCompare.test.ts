import { describe, expect, it, vi } from 'vitest';
import {
	DEFAULT_COMPARE_OPTIONS,
	computeCompareLayout,
	compareCaptionLines,
	drawCompareStitch,
	type StitchCompareCell,
	type StitchCompareDrawTarget,
	type StitchCompareInput,
	type StitchCompareOptions
} from './stitch';

function tile(over: Partial<StitchCompareCell> = {}): StitchCompareCell {
	return {
		width: 1024,
		height: 1024,
		failed: false,
		axisValues: { sampler: 'euler', scheduler: 'simple' },
		seed: 4211984,
		seconds: 18.4,
		...over
	};
}

function input(cols: number, rows: number, over: Partial<StitchCompareCell> = {}): StitchCompareInput {
	return {
		cols,
		rows,
		xLabels: Array.from({ length: cols }, (_, i) => `x${i}`),
		yLabels: Array.from({ length: rows }, (_, i) => `row-${i}`),
		cells: Array.from({ length: cols * rows }, () => tile(over))
	};
}

const options = (over: Partial<StitchCompareOptions> = {}): StitchCompareOptions => ({
	...DEFAULT_COMPARE_OPTIONS,
	...over
});

describe('compare caption lines', () => {
	it('lists axis values, seed and time in that order', () => {
		expect(compareCaptionLines(tile(), ['axis', 'seed', 'time'])).toEqual([
			'sampler euler',
			'scheduler simple',
			'seed 4211984',
			'18 s'
		]);
	});

	it('only draws the chosen lines', () => {
		expect(compareCaptionLines(tile(), ['seed'])).toEqual(['seed 4211984']);
		expect(compareCaptionLines(tile(), [])).toEqual([]);
	});

	it('drops lines the cell has no value for', () => {
		expect(compareCaptionLines(tile({ seed: null, seconds: null }), ['seed', 'time'])).toEqual([]);
	});

	it('writes field names with spaces', () => {
		expect(compareCaptionLines(tile({ axisValues: { true_cfg_scale: '4.5' } }), ['axis'])).toEqual([
			'true cfg scale 4.5'
		]);
	});
});

describe('compare layout', () => {
	it('puts every cell on its row-major position with the gap between', () => {
		const layout = computeCompareLayout(input(4, 3), options({ axisLabels: false, captions: [], gap: 16 }));
		expect(layout.cells).toHaveLength(12);
		const first = layout.cells[0];
		const right = layout.cells[1];
		const below = layout.cells[4];
		expect(right.image.x - first.image.x).toBe(first.image.width + 16);
		expect(below.image.y - first.image.y).toBe(first.image.height + 16);
		expect(layout.cells[7]).toMatchObject({ column: 3, row: 1 });
	});

	it('sizes the canvas to cells, gaps and the outer margin', () => {
		const layout = computeCompareLayout(input(4, 3), options({ axisLabels: false, captions: [], gap: 16, tileMaxSide: 512 }));
		expect(layout.width).toBe(16 + 4 * (512 + 16));
		expect(layout.height).toBe(16 + 3 * (512 + 16));
	});

	it('adds a label row on top and a label column on the left', () => {
		const bare = computeCompareLayout(input(3, 2), options({ axisLabels: false, captions: [] }));
		const labelled = computeCompareLayout(input(3, 2), options({ axisLabels: true, captions: [] }));
		expect(labelled.width).toBeGreaterThan(bare.width);
		expect(labelled.height).toBeGreaterThan(bare.height);
		expect(labelled.xLabels).toHaveLength(3);
		expect(labelled.yLabels).toHaveLength(2);
		expect(bare.xLabels).toHaveLength(0);
		const firstCell = labelled.cells[0].image;
		expect(labelled.xLabels[0].rect.x).toBe(firstCell.x);
		expect(labelled.xLabels[0].rect.y + labelled.xLabels[0].rect.height).toBeLessThanOrEqual(firstCell.y);
		expect(labelled.yLabels[0].rect.x + labelled.yLabels[0].rect.width).toBeLessThanOrEqual(firstCell.x);
		expect(labelled.yLabels[1].rect.y).toBe(labelled.cells[3].image.y);
	});

	it('widens the label column for longer row labels', () => {
		const short = input(2, 1);
		short.yLabels = ['a'];
		const long = input(2, 1);
		long.yLabels = ['dpmpp_2m_sde_gpu'];
		const a = computeCompareLayout(short, options({ captions: [] }));
		const b = computeCompareLayout(long, options({ captions: [] }));
		expect(b.yLabels[0].rect.width).toBeGreaterThan(a.yLabels[0].rect.width);
	});

	it('reserves caption height only when something is drawn under the cells', () => {
		const plain = computeCompareLayout(input(2, 2), options({ captions: [], axisLabels: false }));
		const captioned = computeCompareLayout(input(2, 2), options({ captions: ['axis'], axisLabels: false }));
		expect(plain.cells[0].caption.height).toBe(0);
		expect(captioned.cells[0].caption.height).toBeGreaterThan(0);
		expect(captioned.height).toBeGreaterThan(plain.height);
		expect(captioned.cells[0].lines).toEqual(['sampler euler', 'scheduler simple']);
	});

	it('hides failed cells entirely when skipping them but keeps their position', () => {
		const source = input(2, 1);
		source.cells[1] = tile({ failed: true });
		const kept = computeCompareLayout(source, options({ skipFailed: false }));
		const skipped = computeCompareLayout(source, options({ skipFailed: true }));
		expect(kept.cells[1].hidden).toBe(false);
		expect(skipped.cells[1].hidden).toBe(true);
		expect(skipped.cells[1].lines).toEqual([]);
		expect(skipped.cells[1].image.x).toBe(kept.cells[1].image.x);
	});

	it('scales tiles down to the chosen side', () => {
		const layout = computeCompareLayout(input(2, 1), options({ tileMaxSide: 512 }));
		expect(layout.cells[0].image.width).toBe(512);
		expect(layout.tileMaxSide).toBe(512);
		expect(layout.clamped).toBe(false);
	});

	it('steps the tile size down when the canvas would be too big', () => {
		const layout = computeCompareLayout(input(12, 12, { width: 4096, height: 4096 }), options({ tileMaxSide: 'original' }));
		expect(layout.clamped).toBe(true);
		expect(layout.width * layout.height).toBeLessThanOrEqual(80_000_000);
	});

	it('gives a failed cell a square footprint when no image ever loaded', () => {
		const layout = computeCompareLayout(input(2, 1, { width: 0, height: 0, failed: true }), options({}));
		expect(layout.cells[0].image.width).toBeGreaterThan(0);
		expect(layout.cells[0].image.width).toBe(layout.cells[0].image.height);
	});
});

function recordingTarget() {
	const calls: Array<[string, ...unknown[]]> = [];
	const target = {
		fillStyle: '',
		strokeStyle: '',
		font: '',
		textAlign: 'left',
		textBaseline: 'alphabetic',
		clearRect: (...a: unknown[]) => calls.push(['clearRect', ...a]),
		fillRect: (...a: unknown[]) => calls.push(['fillRect', ...a]),
		strokeRect: (...a: unknown[]) => calls.push(['strokeRect', ...a]),
		drawImage: (...a: unknown[]) => calls.push(['drawImage', ...a]),
		fillText: (...a: unknown[]) => calls.push(['fillText', ...a])
	} as unknown as StitchCompareDrawTarget;
	return { target, calls };
}

describe('compare draw', () => {
	const image = {} as CanvasImageSource;

	it('draws uppercase axis labels, every image and the captions', () => {
		const source = input(2, 1);
		source.xLabels = ['euler', 'er_sde'];
		source.yLabels = ['simple'];
		const opts = options({ captions: ['seed'] });
		const layout = computeCompareLayout(source, opts);
		const { target, calls } = recordingTarget();
		drawCompareStitch(target, [image, image], layout, opts);
		const texts = calls.filter((c) => c[0] === 'fillText').map((c) => c[1]);
		expect(texts).toEqual(expect.arrayContaining(['EULER', 'ER_SDE', 'SIMPLE', 'seed 4211984']));
		expect(calls.filter((c) => c[0] === 'drawImage')).toHaveLength(2);
	});

	it('draws a tinted FAILED tile instead of an image for a failed cell', () => {
		const source = input(2, 1);
		source.cells[1] = tile({ failed: true });
		const opts = options({ captions: [] });
		const layout = computeCompareLayout(source, opts);
		const { target, calls } = recordingTarget();
		drawCompareStitch(target, [image, null], layout, opts);
		expect(calls.filter((c) => c[0] === 'drawImage')).toHaveLength(1);
		expect(calls.some((c) => c[0] === 'fillText' && c[1] === 'FAILED')).toBe(true);
		expect(calls.some((c) => c[0] === 'strokeRect')).toBe(true);
	});

	it('leaves a skipped failed cell blank', () => {
		const source = input(2, 1);
		source.cells[1] = tile({ failed: true });
		const opts = options({ captions: [], skipFailed: true });
		const layout = computeCompareLayout(source, opts);
		const { target, calls } = recordingTarget();
		drawCompareStitch(target, [image, null], layout, opts);
		expect(calls.some((c) => c[0] === 'fillText' && c[1] === 'FAILED')).toBe(false);
		expect(calls.some((c) => c[0] === 'strokeRect')).toBe(false);
	});

	it('clears instead of filling a transparent background', () => {
		const opts = options({ background: 'transparent', axisLabels: false, captions: [] });
		const layout = computeCompareLayout(input(1, 1), opts);
		const { target, calls } = recordingTarget();
		drawCompareStitch(target, [image], layout, opts);
		expect(calls[0][0]).toBe('clearRect');
	});

	it('truncates long text to the cell width through maxWidth', () => {
		const opts = options({ captions: ['axis'] });
		const layout = computeCompareLayout(input(1, 1), opts);
		const fillText = vi.fn();
		const { target } = recordingTarget();
		(target as unknown as { fillText: typeof fillText }).fillText = fillText;
		drawCompareStitch(target, [image], layout, opts);
		for (const call of fillText.mock.calls) expect(call[3]).toBeGreaterThan(0);
	});
});
