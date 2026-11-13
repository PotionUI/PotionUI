import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';

const copyText = vi.fn(async (_text: string) => true);
vi.mock('$lib/utils/clipboard', () => ({ copyText: (text: string) => copyText(text) }));

const { default: ParametersView } = await import('$lib/components/generation/ParametersView.svelte');
const { mountComponent } = await import('./helpers/mount');
const { forgetFoldPreference, PARAMETERS_FOLD_KEY } = await import(
	'$lib/components/generation/parametersFold'
);

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function manyParams(shorts: number, withLong = true): Record<string, unknown> {
	const params: Record<string, unknown> = {};
	if (withLong) params.brief = 'b'.repeat(200);
	for (let i = 0; i < shorts; i++) params[`p${i}`] = i + 1;
	return params;
}

const foldButton = () =>
	[...document.body.querySelectorAll('button')].find((b) => /^(Show all|Show less)/.test(b.textContent ?? '')) as
		| HTMLButtonElement
		| undefined;
const rows = () => [...document.body.querySelectorAll('[data-param-cell]')];
const clickSync = (el: Element) => {
	(el as HTMLElement).click();
	flushSync();
};

beforeEach(() => {
	localStorage.clear();
	forgetFoldPreference();
	copyText.mockClear();
});

afterEach(() => {
	vi.restoreAllMocks();
	forgetFoldPreference();
});

describe('ParametersView', () => {
	it('renders an old generation with no meta and no order: humanized labels, no units, response order', () => {
		mountComponent(ParametersView, {
			params: { magic_number: 7, clipLength: 5, enhance: false, resolution: '768x1344' }
		});
		const text = rows().map((r) => r.textContent?.replace(/\s+/g, ' ').trim());
		expect(text[0]).toContain('Magic number');
		expect(text[0]).toContain('7');
		expect(text[1]).toContain('Clip length');
		expect(text[2]).toContain('Enhance');
		expect(text[2]).toContain('Off');
		expect(text[3]).toContain('768 × 1344');
	});

	it('uses declared labels, units and order', () => {
		mountComponent(ParametersView, {
			params: { b: 4, a: 24 },
			meta: { b: { label: 'Length', unit: 's' }, a: { label: 'Frame rate', unit: 'fps' } },
			order: ['b', 'a']
		});
		const text = rows().map((r) => r.textContent?.replace(/\s+/g, ' ').trim());
		expect(text[0]).toContain('Length');
		expect(text[0]).toContain('4 s');
		expect(text[1]).toContain('Frame rate');
		expect(text[1]).toContain('24 fps');
	});

	it('shows "No parameters" when everything is hidden', () => {
		mountComponent(ParametersView, { params: { a: '', b: null, c: [], d: 'euler' }, models: [{ name: 'euler' }] });
		expect(document.body.textContent).toContain('No parameters');
		expect(rows()).toHaveLength(0);
	});

	it('shows a spinner while loading and no rows', () => {
		mountComponent(ParametersView, { params: { a: 1 }, loading: true });
		expect(document.body.querySelector('[role="status"]')).not.toBeNull();
		expect(rows()).toHaveLength(0);
	});

	it('renders the axis tag next to the label, on a row and on a block', () => {
		mountComponent(ParametersView, {
			params: { sampler: 'er_sde', story: 'x'.repeat(100) },
			axisTags: { sampler: 'X', story: 'Y' }
		});
		const tags = [...document.body.querySelectorAll('[data-testid="axis-tag"]')];
		expect(tags.map((t) => t.textContent?.trim())).toEqual(['Y', 'X']);
		expect(tags[1].closest('[data-param-cell]')?.textContent).toContain('er_sde');
	});

	it('hides a value that duplicates a model', () => {
		mountComponent(ParametersView, {
			params: { checkpoint: 'model_a.safetensors', sampler: 'euler' },
			models: [{ name: 'Model A', filename: 'model_a.safetensors' }]
		});
		expect(rows()).toHaveLength(1);
		expect(document.body.textContent).not.toContain('model_a');
	});

	it('copies the raw value of a short row, not the formatted one', async () => {
		mountComponent(ParametersView, { params: { size: '768x1344' } });
		const button = document.body.querySelector('button[aria-label="Copy Size"]') as HTMLButtonElement;
		expect(rows()[0].textContent).toContain('768 × 1344');
		button.click();
		await flush();
		expect(copyText).toHaveBeenCalledWith('768x1344');
	});

	it('copies the raw text of a long block and keeps its copy button visible without hover', async () => {
		const text = 'line one\n' + 'z'.repeat(120);
		mountComponent(ParametersView, { params: { notes: text } });
		const button = document.body.querySelector('section button[aria-label="Copy Notes"]') as HTMLButtonElement;
		expect(button.className).not.toContain('opacity-0');
		button.click();
		await flush();
		expect(copyText).toHaveBeenCalledWith(text);
	});

	it('makes row copy buttons reachable on touch and keyboard focus', () => {
		mountComponent(ParametersView, { params: { a: 1 } });
		const button = document.body.querySelector('dl button') as HTMLButtonElement;
		expect(button.className).toContain('[@media(hover:none)]:opacity-100');
		expect(button.className).toContain('group-focus-within:opacity-100');
	});
});

describe('ParametersView tiles', () => {
	const grid = () => document.body.querySelector('dl') as HTMLElement;
	const span = (cell: Element) => (cell as HTMLElement).style.gridColumn;

	it('uses three columns in a 400 px column and leaves the last cell of an odd count empty', () => {
		vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(() => 368);
		mountComponent(ParametersView, { params: { a: 1, b: 2, c: 3, d: 4 } });
		flushSync();
		expect(grid().getAttribute('data-columns')).toBe('3');
		expect(grid().style.gridTemplateColumns).toContain('repeat(3');
		expect(rows()).toHaveLength(4);
	});

	it('uses two columns on a very narrow container', () => {
		vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(() => 260);
		mountComponent(ParametersView, { params: { a: 1 } });
		flushSync();
		expect(grid().getAttribute('data-columns')).toBe('2');
	});

	it('spans two or three columns for long values and never more than the columns', () => {
		vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(() => 368);
		mountComponent(ParametersView, {
			params: { short: 'euler', mid: 'dpmpp_2m_sde_gpu_karras', long: 'dpmpp_2m_sde_gpu_karras_extra_long_name' }
		});
		flushSync();
		const cells = rows();
		expect(span(cells[0])).toContain('span 1');
		expect(span(cells[1])).toContain('span 2');
		expect(span(cells[2])).toContain('span 3');
	});

	it('puts the value above its label in bold, mono for numbers and sans for words', () => {
		mountComponent(ParametersView, { params: { steps: 24, sampler: 'euler' } });
		const [steps, sampler] = rows();
		expect(steps.querySelector('dt')?.textContent).toContain('Steps');
		expect(steps.className).toContain('flex-col-reverse');
		const value = steps.querySelector('dd') as HTMLElement;
		expect(value.textContent?.trim()).toBe('24');
		expect(value.className).toContain('font-semibold');
		expect(value.className).toContain('font-mono');
		expect(sampler.querySelector('dd')?.className).not.toContain('font-mono');
	});

	it('writes the unit beside the value in muted weight', () => {
		mountComponent(ParametersView, { params: { duration: 4 }, meta: { duration: { unit: 's' } } });
		const value = rows()[0].querySelector('dd') as HTMLElement;
		expect(value.textContent?.replace(/\s+/g, ' ').trim()).toBe('4 s');
		expect(value.querySelector('span')?.className).toContain('text-fg-muted');
	});

	it('shows Off at normal colour', () => {
		mountComponent(ParametersView, { params: { enhance: false } });
		const value = rows()[0].querySelector('dd') as HTMLElement;
		expect(value.textContent?.trim()).toBe('Off');
		expect(value.className).not.toContain('text-fg-muted');
	});

	it('folds by whole rows at three columns and four columns', async () => {
		let width = 368;
		vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(() => width);
		const view = mountComponent(ParametersView, { params: manyParams(14, false) });
		flushSync();
		expect(grid().getAttribute('data-columns')).toBe('3');
		expect(rows()).toHaveLength(9);
		view.destroy();
		width = 680;
		mountComponent(ParametersView, { params: manyParams(14, false) });
		flushSync();
		expect(grid().getAttribute('data-columns')).toBe('4');
		expect(rows()).toHaveLength(8);
	});
});

describe('ParametersView quote blocks', () => {
	it('draws long text as a quote with a left rule and no filled box', () => {
		mountComponent(ParametersView, { params: { notes: 'q'.repeat(200) } });
		const block = document.body.querySelector('[data-testid="parameter-block"]') as HTMLElement;
		const quote = block.querySelector('div.border-l-2') as HTMLElement;
		expect(quote.className).toContain('border-line-strong');
		expect(quote.className).toContain('pl-3');
		expect(block.innerHTML).not.toContain('bg-surface-2');
		expect(block.querySelector('.clamp')?.className).toContain('leading-relaxed');
	});

	it('keeps the copy button visible without hover on a quote block', () => {
		mountComponent(ParametersView, { params: { notes: 'q'.repeat(200) } });
		const button = document.body.querySelector('[data-testid="parameter-block"] button[aria-label="Copy Notes"]') as HTMLElement;
		expect(button.className).not.toContain('opacity-0');
	});
});

describe('ParametersView text blocks', () => {
	function stubLayout(scrollHeight: number, clientHeight: number) {
		vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(scrollHeight);
		vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(clientHeight);
	}
	const showMore = () =>
		[...document.body.querySelectorAll('section button')].find((b) => /^Show (more|less)$/.test(b.textContent?.trim() ?? '')) as
			| HTMLButtonElement
			| undefined;

	it('offers "Show more" only when the text overflows', () => {
		stubLayout(300, 88);
		mountComponent(ParametersView, { params: { notes: 'q'.repeat(300) } });
		expect(showMore()?.textContent?.trim()).toBe('Show more');
	});

	it('does not offer "Show more" when the text fits', () => {
		stubLayout(88, 88);
		mountComponent(ParametersView, { params: { notes: 'q'.repeat(300) } });
		expect(showMore()).toBeUndefined();
	});

	it('toggles between Show more and Show less and keeps aria-expanded in step', () => {
		stubLayout(300, 88);
		mountComponent(ParametersView, { params: { notes: 'q'.repeat(300) } });
		const button = showMore() as HTMLButtonElement;
		expect(button.getAttribute('aria-expanded')).toBe('false');
		clickSync(button);
		expect(showMore()?.textContent?.trim()).toBe('Show less');
		expect(showMore()?.getAttribute('aria-expanded')).toBe('true');
		clickSync(showMore() as HTMLButtonElement);
		expect(showMore()?.textContent?.trim()).toBe('Show more');
	});

	it('measures again when the layout changes after mount', () => {
		let observed: (() => void) | undefined;
		class FakeObserver {
			constructor(cb: () => void) {
				observed = cb;
			}
			observe() {}
			unobserve() {}
			disconnect() {}
		}
		vi.stubGlobal('ResizeObserver', FakeObserver);
		stubLayout(88, 88);
		mountComponent(ParametersView, { params: { notes: 'q'.repeat(300) } });
		expect(showMore()).toBeUndefined();
		vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(400);
		observed?.();
		flushSync();
		expect(showMore()).toBeDefined();
		vi.unstubAllGlobals();
	});

	it('can collapse again after an expanded measurement saw no overflow', () => {
		let observed: (() => void) | undefined;
		class FakeObserver {
			constructor(cb: () => void) {
				observed = cb;
			}
			observe() {}
			unobserve() {}
			disconnect() {}
		}
		vi.stubGlobal('ResizeObserver', FakeObserver);
		stubLayout(300, 88);
		mountComponent(ParametersView, { params: { notes: 'q'.repeat(300) } });
		clickSync(showMore() as HTMLButtonElement);
		vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(300);
		observed?.();
		flushSync();
		expect(showMore()?.textContent?.trim()).toBe('Show less');
		clickSync(showMore() as HTMLButtonElement);
		expect(showMore()?.textContent?.trim()).toBe('Show more');
		vi.unstubAllGlobals();
	});

	it('renders every long text, however many, in field order', () => {
		mountComponent(ParametersView, {
			params: { b: 'b'.repeat(90), a: 'a'.repeat(90), c: 'c'.repeat(90) },
			order: ['c', 'a', 'b']
		});
		const labels = [...document.body.querySelectorAll('section > div:first-child > span:first-child')].map((e) => e.textContent);
		expect(labels).toEqual(['C', 'A', 'B']);
	});
});

describe('ParametersView fold', () => {
	it('does not fold at 10 short values', () => {
		mountComponent(ParametersView, { params: manyParams(10) });
		expect(foldButton()).toBeUndefined();
		expect(rows()).toHaveLength(10);
	});

	it('folds 23 short values to 8 and expands in place', () => {
		mountComponent(ParametersView, { params: manyParams(23) });
		const button = foldButton() as HTMLButtonElement;
		expect(rows()).toHaveLength(8);
		expect(button.textContent?.trim()).toBe('Show all (23)');
		expect(button.getAttribute('aria-expanded')).toBe('false');
		expect(document.getElementById(button.getAttribute('aria-controls') as string)).not.toBeNull();
		expect(document.body.querySelectorAll('section')).toHaveLength(1);

		clickSync(button);
		expect(rows()).toHaveLength(23);
		expect(foldButton()?.textContent?.trim()).toBe('Show less');
		expect(foldButton()?.getAttribute('aria-expanded')).toBe('true');
		expect(document.body.textContent).toContain('P22');

		clickSync(foldButton() as HTMLButtonElement);
		expect(rows()).toHaveLength(8);
		expect(foldButton()?.textContent?.trim()).toBe('Show all (23)');
	});

	it('is a real, focusable button', () => {
		mountComponent(ParametersView, { params: manyParams(11) });
		const button = foldButton() as HTMLButtonElement;
		expect(button.tagName).toBe('BUTTON');
		expect(button.type).toBe('button');
		expect(button.tabIndex).toBe(0);
		button.focus();
		expect(document.activeElement).toBe(button);
	});

	it('stores the choice and restores it on the next mount', () => {
		const first = mountComponent(ParametersView, { params: manyParams(12) });
		clickSync(foldButton() as HTMLButtonElement);
		expect(localStorage.getItem(PARAMETERS_FOLD_KEY)).toBe('1');
		first.destroy();
		forgetFoldPreference();
		mountComponent(ParametersView, { params: manyParams(12) });
		expect(rows()).toHaveLength(12);
		expect(foldButton()?.getAttribute('aria-expanded')).toBe('true');
	});

	it('updates every fold button on screen from the one preference', () => {
		mountComponent(ParametersView, { params: manyParams(12) });
		mountComponent(ParametersView, { params: manyParams(14) });
		const buttons = () =>
			[...document.body.querySelectorAll('button')].filter((b) => /^(Show all|Show less)/.test(b.textContent ?? ''));
		clickSync(buttons()[0]);
		expect(buttons().map((b) => b.getAttribute('aria-expanded'))).toEqual(['true', 'true']);
	});

	it('still works when localStorage is blocked', () => {
		vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
			throw new Error('blocked');
		});
		vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
			throw new Error('blocked');
		});
		expect(() => mountComponent(ParametersView, { params: manyParams(12) })).not.toThrow();
		expect(rows()).toHaveLength(8);
		clickSync(foldButton() as HTMLButtonElement);
		expect(rows()).toHaveLength(12);
	});
});
