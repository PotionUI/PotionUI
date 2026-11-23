// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	headerFit,
	headerFits,
	headerStages,
	pickStage,
	stageSignature,
	type HeaderMeasure,
	type HeaderStage
} from './toolbarFit';

const signatures = (stages: HeaderStage[]) => stages.map(stageSignature);

describe('headerStages', () => {
	it('walks wrap, icons, label-less library, then folds variables before styles', () => {
		expect(signatures(headerStages({ actions: ['styles', 'variables'], tabs: true }))).toEqual([
			'row||',
			'wrap||',
			'wrap|styles variables|',
			'wrap|styles variables library|',
			'wrap|styles variables library|variables',
			'wrap|styles variables library|variables styles'
		]);
	});

	it('never wraps a header that has no tab strip', () => {
		const stages = headerStages({ actions: ['styles', 'variables'], tabs: false });
		expect(stages.some((stage) => stage.wrap)).toBe(false);
		expect(signatures(stages)[0]).toBe('row||');
		expect(signatures(stages)[1]).toBe('row|styles variables|');
	});

	it('only offers to fold what the call site actually renders', () => {
		expect(signatures(headerStages({ actions: ['variables'], tabs: false }))).toEqual([
			'row||',
			'row|variables|',
			'row|variables library|',
			'row|variables library|variables'
		]);
		expect(signatures(headerStages({ actions: [], tabs: false }))).toEqual(['row||', 'row|library|']);
	});

	it('starts at the icon stage in compact mode', () => {
		const stages = headerStages({ actions: ['styles', 'variables'], tabs: false, compact: true });
		expect(stages[0].icons).toEqual(['styles', 'variables']);
		expect(stages[0].folded).toEqual([]);
		expect(headerStages({ actions: [], tabs: false, compact: true })).toHaveLength(1);
	});

	it('only ever gives up more as it goes down the ladder', () => {
		const stages = headerStages({ actions: ['styles', 'variables'], tabs: true });
		for (let i = 1; i < stages.length; i++) {
			const before = stages[i - 1];
			const after = stages[i];
			expect(after.icons).toEqual(expect.arrayContaining(before.icons));
			expect(after.folded).toEqual(expect.arrayContaining(before.folded));
			expect(after.wrap || !before.wrap).toBe(true);
		}
	});
});

describe('pickStage', () => {
	it('stops at the first stage that fits and applies only the stages it tried', () => {
		const applied: number[] = [];
		let current = 0;
		const index = pickStage(
			[0, 1, 2, 3],
			(stage) => {
				current = stage;
				applied.push(stage);
			},
			() => current >= 2
		);
		expect(index).toBe(2);
		expect(applied).toEqual([0, 1, 2]);
	});

	it('settles on the last stage when nothing fits', () => {
		const applied: number[] = [];
		const index = pickStage([0, 1, 2], (stage) => applied.push(stage), () => false);
		expect(index).toBe(2);
		expect(applied.at(-1)).toBe(2);
	});

	it('does nothing for an empty ladder', () => {
		const apply = vi.fn();
		expect(pickStage([], apply, () => true)).toBe(-1);
		expect(apply).not.toHaveBeenCalled();
	});
});

describe('headerFits', () => {
	const base: HeaderMeasure = { scrollWidth: 400, clientWidth: 400, wrapped: false, strip: null };

	it('fails when the header overflows by more than a pixel', () => {
		expect(headerFits(base)).toBe(true);
		expect(headerFits({ ...base, scrollWidth: 401 })).toBe(true);
		expect(headerFits({ ...base, scrollWidth: 402 })).toBe(false);
	});

	it('keeps the tab strip at 220px or its whole content, whichever is smaller', () => {
		expect(headerFits({ ...base, strip: { clientWidth: 219, scrollWidth: 500 } })).toBe(false);
		expect(headerFits({ ...base, strip: { clientWidth: 220, scrollWidth: 500 } })).toBe(true);
		expect(headerFits({ ...base, strip: { clientWidth: 96, scrollWidth: 96 } })).toBe(true);
		expect(headerFits({ ...base, strip: { clientWidth: 95, scrollWidth: 96 } })).toBe(false);
	});

	it('gives the strip its own row once wrapped', () => {
		expect(headerFits({ ...base, wrapped: true, strip: { clientWidth: 80, scrollWidth: 500 } })).toBe(true);
	});
});

describe('headerFit', () => {
	let resizeCallbacks: Array<() => void> = [];

	class StubResizeObserver {
		constructor(private callback: () => void) {}
		observe() {
			resizeCallbacks.push(this.callback);
		}
		unobserve() {}
		disconnect() {}
	}

	beforeEach(() => {
		resizeCallbacks = [];
		vi.stubGlobal('ResizeObserver', StubResizeObserver);
	});

	afterEach(() => {
		vi.unstubAllGlobals();
		document.body.innerHTML = '';
	});

	const NEED: Record<string, number> = {
		'row||': 700,
		'wrap||': 500,
		'wrap|styles variables|': 380,
		'wrap|styles variables library|': 300,
		'wrap|styles variables library|variables': 240,
		'wrap|styles variables library|variables styles': 180
	};

	function header() {
		const node = document.createElement('header');
		document.body.appendChild(node);
		let width = 1000;
		const signature = () =>
			`${node.dataset.wrap ? 'wrap' : 'row'}|${node.dataset.icons ?? ''}|${node.dataset.folded ?? ''}`;
		Object.defineProperty(node, 'clientWidth', { configurable: true, get: () => width });
		const fittingWhileMeasuring: boolean[] = [];
		Object.defineProperty(node, 'scrollWidth', {
			configurable: true,
			get: () => {
				fittingWhileMeasuring.push(node.classList.contains('is-fitting'));
				return Math.max(NEED[signature()] ?? 0, width);
			}
		});
		return {
			node,
			fittingWhileMeasuring,
			resize(next: number) {
				width = next;
				resizeCallbacks.forEach((callback) => callback());
			}
		};
	}

	const settle = async () => {
		await Promise.resolve();
		await Promise.resolve();
	};

	const stages = headerStages({ actions: ['styles', 'variables'], tabs: true });

	it('writes the chosen stage onto the header and reports it once', async () => {
		const { node } = header();
		const onChange = vi.fn();
		headerFit(node, { stages, onChange });
		await settle();

		expect(node.dataset.wrap).toBeUndefined();
		expect(node.dataset.icons).toBe('');
		expect(onChange).toHaveBeenCalledTimes(1);
		expect(stageSignature(onChange.mock.calls[0][0])).toBe('row||');
	});

	it('walks down the ladder as the header narrows and back up as it widens', async () => {
		const { node, resize } = header();
		const onChange = vi.fn();
		headerFit(node, { stages, onChange });
		await settle();

		const reported = () => onChange.mock.calls.map((call) => stageSignature(call[0]));
		resize(450);
		expect(node.dataset.wrap).toBe('1');
		expect(node.dataset.icons).toBe('styles variables');
		resize(290);
		expect(node.dataset.icons).toBe('styles variables library');
		expect(node.dataset.folded).toBe('variables');
		resize(150);
		expect(node.dataset.folded).toBe('variables styles');
		resize(1000);
		expect(node.dataset.wrap).toBeUndefined();
		expect(node.dataset.folded).toBe('');
		expect(reported()).toEqual([
			'row||',
			'wrap|styles variables|',
			'wrap|styles variables library|variables',
			'wrap|styles variables library|variables styles',
			'row||'
		]);
	});

	it('does not report a stage it is already in', async () => {
		const { node, resize } = header();
		const onChange = vi.fn();
		headerFit(node, { stages, onChange });
		await settle();
		resize(990);
		resize(980);
		expect(onChange).toHaveBeenCalledTimes(1);
	});

	it('does not re-measure when update brings an equivalent ladder, and does when it changes', async () => {
		const { node, fittingWhileMeasuring } = header();
		const action = headerFit(node, { stages, onChange: () => {} });
		await settle();
		const measured = fittingWhileMeasuring.length;
		action.update({ stages: headerStages({ actions: ['styles', 'variables'], tabs: true }), onChange: () => {} });
		await settle();
		expect(fittingWhileMeasuring.length).toBe(measured);
		action.update({ stages: headerStages({ actions: ['styles'], tabs: true }), onChange: () => {} });
		await settle();
		expect(fittingWhileMeasuring.length).toBeGreaterThan(measured);
	});

	it('measures with transitions switched off and switches them back on afterwards', async () => {
		const { node, resize, fittingWhileMeasuring } = header();
		headerFit(node, { stages, onChange: () => {} });
		await settle();
		resize(300);

		expect(fittingWhileMeasuring.length).toBeGreaterThan(0);
		expect(fittingWhileMeasuring.every(Boolean)).toBe(true);
		expect(node.classList.contains('is-fitting')).toBe(false);
	});

	it('re-fits when new stages arrive', async () => {
		const { node } = header();
		const action = headerFit(node, { stages: headerStages({ actions: [], tabs: false }), onChange: () => {} });
		await settle();
		expect(node.dataset.icons).toBe('');
		action.update({ stages, onChange: () => {} });
		await settle();
		expect(node.dataset.icons).toBe('');
		action.destroy();
	});

	it('ignores a header that has no width yet', async () => {
		const node = document.createElement('header');
		document.body.appendChild(node);
		const onChange = vi.fn();
		headerFit(node, { stages, onChange });
		await settle();
		expect(onChange).not.toHaveBeenCalled();
		expect(node.dataset.icons).toBeUndefined();
	});
});
