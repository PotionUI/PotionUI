import { afterEach, describe, expect, it } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { emptySlice, type SliceParams } from '$lib/tool-tabs/image/slice/model';

const { default: SliceOptions } = await import('$lib/components/tool-tabs/image/slice/SliceOptions.svelte');

function params(cells = 0): SliceParams {
	return {
		...emptySlice('mira-sheet'),
		cells: Array.from({ length: cells }, (_, i) => ({ id: `c${i}`, x0: i / cells, x1: (i + 1) / cells, y0: 0, y1: 1, ...(i === cells - 1 ? { skip: true } : {}) }))
	};
}

function render(props: Record<string, unknown> = {}) {
	const calls: unknown[][] = [];
	const log =
		(name: string) =>
		(...args: unknown[]) =>
			calls.push([name, ...args]);
	const view = mountComponent<Record<string, unknown>>(SliceOptions, {
		params: params(),
		onpreset: log('preset'),
		ondetect: log('detect'),
		onsnap: log('snap'),
		onoutput: log('output'),
		onpattern: log('pattern'),
		onrename: log('rename'),
		onskip: log('skip'),
		oncollection: log('collection'),
		onadd: log('add'),
		onclear: log('clear'),
		oncell: log('cell'),
		...props
	});
	const q = <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel);
	const all = <T extends Element = HTMLElement>(sel: string) => Array.from(view.target.querySelectorAll<T>(sel));
	return { calls, view, q, all };
}

afterEach(() => {
	document.body.innerHTML = '';
});

describe('Slice options, layout', () => {
	it('opens on Layout with Detect first and the presets labelled columns by rows', () => {
		const { q, all, calls } = render();
		expect(q('[role="tablist"]')).toBeNull();
		expect(q('[data-slice-detect]')?.textContent).toContain('Detect panels');
		const presets = all<HTMLButtonElement>('[data-slice-preset]');
		expect(presets.map((p) => p.textContent?.trim())).toEqual(['2×1', '2×2', '3×2', '3×3', '4×4']);
		expect(presets[2].getAttribute('aria-label')).toBe('3 across, 2 down');
		presets[2].click();
		q<HTMLButtonElement>('[data-slice-detect]')!.click();
		q<HTMLButtonElement>('[data-slice-add]')!.click();
		expect(calls).toEqual([['preset', 3, 2], ['detect'], ['add']]);
	});

	it('marks the active preset and reports the detection summary', () => {
		const { q } = render({ params: { ...params(6), layout: { preset: '3x2', cols: 3, rows: 2, snap: true, detected: '6 panels found.' } } });
		expect(q('[data-slice-preset="3x2"]')!.getAttribute('aria-pressed')).toBe('true');
		expect(q('[data-slice-detect]')!.textContent).toContain('Detect again');
		expect(q('[data-slice-detected]')!.textContent).toContain('6 panels found.');
	});

	it('warns softly when cells overlap', () => {
		const { q } = render({ params: params(2), overlap: true });
		expect(q('[data-slice-overlap]')).not.toBeNull();
	});
});

describe('Slice options, output', () => {
	const names = [
		{ id: 'c0', n: '01', name: 'mira-sheet-01', typed: false, thumb: null, skip: false },
		{ id: 'c1', n: '02', name: 'mira-sheet-02', typed: false, thumb: null, skip: false },
		{ id: 'c2', n: '—', name: '', typed: false, thumb: null, skip: true }
	];

	it('shows the kept count in the Output tab and one row per cell', () => {
		const { q, all } = render({ params: params(3), names, tab: 'output', fill: { hex: 'E9E5DE', rgb: '233 229 222' } });
		expect(q('[data-slice-tab="output"]')!.textContent).toContain('2');
		expect(all('[data-slice-name-row]').map((row) => row.dataset.sliceNameRow)).toEqual(['01', '02', 'skipped']);
		expect(q('[data-slice-fill]')!.textContent).toContain('E9E5DE');
		expect(q('[data-slice-count]')!.textContent).toContain('2 images · PNG · Library');
		const skipped = all<HTMLInputElement>('[data-slice-name-row] input')[2];
		expect(skipped.disabled).toBe(true);
		expect(skipped.placeholder).toBe('Skipped');
	});

	it('inserts a token, skips a row and renames a cell', () => {
		const { all, calls } = render({ params: params(3), names, tab: 'output' });
		all<HTMLButtonElement>('[aria-label="Insert a token"] button').find((b) => b.textContent === '{row}')!.click();
		all<HTMLButtonElement>('[data-slice-name-skip]')[1].click();
		const input = all<HTMLInputElement>('[data-slice-name-row] input')[0];
		input.value = 'hero';
		input.dispatchEvent(new Event('change', { bubbles: true }));
		flushSync();
		expect(calls).toEqual([
			['pattern', '{sheet}-{n}{row}'],
			['skip', 'c1'],
			['rename', 'c0', 'hero']
		]);
	});

	it('toggles trim and changes the aspect', () => {
		const { q, all, calls } = render({ params: params(2), tab: 'output' });
		q<HTMLButtonElement>('[data-slice-section="cells"] [role="switch"]')!.click();
		all<HTMLButtonElement>('[data-slice-section="cells"] button').find((b) => b.textContent?.trim() === 'Square')!.click();
		expect(calls).toEqual([
			['output', { trim: true }],
			['output', { aspect: 'square' }]
		]);
	});
});

describe('Slice options on a phone', () => {
	it('shows Detect and the presets in one row with the kept count', () => {
		const { q, all } = render({ phone: true, params: params(3) });
		expect(q('[data-slice-phone-layout]')).not.toBeNull();
		expect(all('[data-slice-phone-layout] button')).toHaveLength(6);
		expect(q('[data-slice-kept]')!.textContent).toBe('2 of 3 kept');
	});

	it('swaps to the cell actions when a cell is selected and offers Merge for two', () => {
		const one = render({ phone: true, params: params(3), selected: ['c0'], selectedNumber: '01' });
		expect(one.q('[data-slice-phone-cell]')!.textContent).toContain('Cell 01');
		Array.from(one.view.target.querySelectorAll<HTMLButtonElement>('[data-slice-phone-cell] button')).find((b) => b.textContent?.includes('Split'))!.click();
		expect(one.calls).toEqual([['cell', 'split']]);
		const two = render({ phone: true, params: params(3), selected: ['c0', 'c1'] });
		const buttons = Array.from(two.view.target.querySelectorAll<HTMLButtonElement>('[data-slice-phone-cell] button'));
		expect(buttons.map((b) => b.textContent?.trim())).toEqual(['Merge']);
	});
});
