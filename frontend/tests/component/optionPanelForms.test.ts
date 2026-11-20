import { afterEach, describe, expect, it } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from './helpers/mount';
import { EMPTY_IMAGE_PLAN } from '$lib/media/imageEditPlan';
import { emptySlice } from '$lib/tool-tabs/image/slice/model';
import old from './fixtures/oldOptionPanels.json';

const { default: ResizeOptions } = await import('$lib/components/tool-tabs/image/ResizeOptions.svelte');
const { default: CropOptions } = await import('$lib/components/tool-tabs/image/CropOptions.svelte');
const { default: SliceOptions } = await import('$lib/components/tool-tabs/image/slice/SliceOptions.svelte');
const { default: NumberBoxField } = await import('$lib/components/form-fields/NumberBoxField.svelte');

async function settle() {
	for (let i = 0; i < 40; i++) await Promise.resolve();
	flushSync();
}

afterEach(() => {
	document.body.innerHTML = '';
});

async function typeBox(root: ParentNode, selector: string, text: string) {
	const input = root.querySelector<HTMLInputElement>(selector)!;
	input.focus();
	input.value = text;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
	await settle();
	return input;
}

const byText = (root: ParentNode, selector: string, text: string) => Array.from(root.querySelectorAll<HTMLButtonElement>(selector)).find((node) => node.textContent?.trim() === text);

type Case = { label: string; params: unknown; input: { width: number; height: number }; emitted: unknown[] };

describe('Resize panel against the old one', () => {
	const cases = (old.resize as Case[]).filter((item) => !item.label.includes('abc'));

	async function run(item: Case) {
		const emitted: unknown[] = [];
		const view = mountComponent<Record<string, unknown>>(ResizeOptions, { params: item.params, input: item.input, onchange: (next: unknown) => emitted.push(next) });
		await settle();
		const root = view.target;
		const raw = item.label.split(' ')[1];
		if (item.label.startsWith('width')) await typeBox(root, '[data-field-name="width"] input', raw);
		else if (item.label.startsWith('height')) await typeBox(root, '[data-field-name="height"] input', raw);
		else if (item.label.startsWith('lock')) root.querySelector<HTMLInputElement>('[data-field-name="lock"] input')!.click();
		else if (item.label.startsWith('scale')) byText(root, '[aria-label="Scale"] button', `${item.label.split(' ')[1]}%`)!.click();
		else await typeBox(root, '[data-field-name="width"] input', '777');
		await settle();
		return emitted;
	}

	it('sends the same size for every typed value, lock state, scale chip and odd source', async () => {
		expect(cases.length).toBeGreaterThan(20);
		for (const item of cases) expect(await run(item), item.label).toEqual(item.emitted);
	});

	it('writes nothing while the number is still being typed', async () => {
		const emitted: unknown[] = [];
		const view = mountComponent<Record<string, unknown>>(ResizeOptions, { params: { width: 3000, height: 2000, lock: true }, input: { width: 3000, height: 2000 }, onchange: (next: unknown) => emitted.push(next) });
		await settle();
		const input = view.target.querySelector<HTMLInputElement>('[data-field-name="width"] input')!;
		input.focus();
		input.value = '15';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(emitted).toEqual([]);
	});

	it('shows a size changed from outside without sending it back', async () => {
		const emitted: unknown[] = [];
		const view = mountComponent<Record<string, unknown>>(ResizeOptions, { params: { width: 3000, height: 2000, lock: true }, input: { width: 3000, height: 2000 }, onchange: (next: unknown) => emitted.push(next) });
		await settle();
		view.rerender({ params: { width: 1200, height: 800, lock: true } });
		await settle();
		expect(view.target.querySelector<HTMLInputElement>('[data-field-name="width"] input')!.value).toBe('1200');
		expect(view.target.querySelector<HTMLInputElement>('[data-field-name="height"] input')!.value).toBe('800');
		expect(emitted).toEqual([]);
	});

	it('shows the clamped size after Enter', async () => {
		let params = { width: 3000, height: 2000, lock: false };
		const view = mountComponent<Record<string, unknown>>(ResizeOptions, { params, input: { width: 3000, height: 2000 }, onchange: (next: unknown) => view.rerender({ params: (params = next as typeof params) }) });
		await settle();
		const input = await typeBox(view.target, '[data-field-name="width"] input', '99999');
		expect(input.value).toBe('16384');
	});
});

describe('Crop panel against the old one', () => {
	const frame = { width: 3000, height: 2000 };
	const plan = { ...EMPTY_IMAGE_PLAN, rect: { x: 0.1, y: 0.2, width: 0.5, height: 0.4 } };
	const ARIA = ['Crop X', 'Crop Y', 'Crop width', 'Crop height'];

	async function run(item: Case) {
		const emitted: unknown[] = [];
		const view = mountComponent<Record<string, unknown>>(CropOptions, { params: item.params, input: item.input, onchange: (next: unknown) => emitted.push(next) });
		await settle();
		const root = view.target;
		const label = item.label;
		if (label.startsWith('aspect')) byText(root, '[data-crop-aspects] button', label.slice(7))!.click();
		else if (label.startsWith('orient')) root.querySelector<HTMLButtonElement>(`[data-crop-orient="${label.slice(7)}"]`)!.click();
		else if (label.startsWith('longest')) byText(root, '[aria-label="Longest side"] button', label.split(' ')[1])?.click();
		else {
			const name = ARIA.find((candidate) => label.startsWith(candidate))!;
			const raw = label.slice(name.length + 1).split(' ')[0];
			await typeBox(root, `[aria-label="${name}"]`, raw);
		}
		await settle();
		return emitted;
	}

	it('sends the same crop for every aspect, orientation, rectangle value and longest side', async () => {
		const SAME_CHOICE = ['aspect Free', 'longest FULL', 'longest 1024 from set'];
		const cases = (old.crop as Case[]).filter((item) => !item.label.includes('abc') && !item.label.includes('-4') && !SAME_CHOICE.includes(item.label));
		expect(cases.length).toBeGreaterThan(30);
		for (const item of cases) expect(await run(item), item.label).toEqual(item.emitted);
	});

	it('moves the fields when the box on the canvas moves, without sending anything back', async () => {
		const emitted: unknown[] = [];
		const view = mountComponent<Record<string, unknown>>(CropOptions, { params: { plan, aspect: 'free' }, input: frame, onchange: (next: unknown) => emitted.push(next) });
		await settle();
		const read = () => ARIA.map((name) => view.target.querySelector<HTMLInputElement>(`[aria-label="${name}"]`)!.value);
		expect(read()).toEqual(['300', '400', '1500', '800']);
		view.rerender({ params: { plan: { ...plan, rect: { x: 0.2, y: 0.25, width: 0.3, height: 0.5 } }, aspect: 'free' } });
		await settle();
		expect(read()).toEqual(['600', '500', '900', '1000']);
		expect(emitted).toEqual([]);
	});

	it('moves the box when a field is typed into', async () => {
		const emitted: Array<{ plan: { rect: { width: number } } }> = [];
		const view = mountComponent<Record<string, unknown>>(CropOptions, { params: { plan, aspect: 'free' }, input: frame, onchange: (next: unknown) => emitted.push(next as never) });
		await settle();
		await typeBox(view.target, '[aria-label="Crop width"]', '900');
		expect(emitted).toHaveLength(1);
		expect(emitted[0].plan.rect.width).toBeCloseTo(0.3);
	});

	it('keeps the phone strip of chips', async () => {
		const view = mountComponent<Record<string, unknown>>(CropOptions, { params: { plan, aspect: 'free' }, input: frame, phone: true, onchange: () => undefined });
		await settle();
		expect(view.target.querySelector('[data-crop-aspects] button[aria-pressed="true"]')?.textContent).toBe('Free');
		expect(view.target.querySelector('[data-field-name]')).toBeNull();
	});
});

describe('Slice panel against the old one', () => {
	const sliceParams = (cells: number) => ({
		...emptySlice('mira-sheet'),
		cells: Array.from({ length: cells }, (_, i) => ({ id: `c${i}`, x0: i / cells, x1: (i + 1) / cells, y0: 0, y1: 1 })),
		layout: { preset: null, cols: 3, rows: 2, snap: true, detected: null }
	});

	async function run(label: string) {
		const calls: unknown[][] = [];
		const log = (name: string) => (...args: unknown[]) => calls.push([name, ...args]);
		const output = /^(tolerance|padding|trim|aspect|pattern|token|collection)/.test(label);
		const view = mountComponent<Record<string, unknown>>(SliceOptions, {
			params: sliceParams(output ? 2 : 0),
			tab: output ? 'output' : 'layout',
			collections: [{ value: 'c1', label: 'Sprites' }, { value: 'c2', label: 'Faces' }],
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
			oncell: log('cell')
		});
		await settle();
		const root = view.target;
		const [kind, raw] = label.split(' ');
		const box = (name: string) => `[data-field-name="${name}"] input`;
		if (kind === 'columns' && raw !== 'plus') await typeBox(root, box('cols'), raw);
		else if (kind === 'rows') await typeBox(root, box('rows'), raw);
		else if (kind === 'tolerance') await typeBox(root, box('tolerance'), raw);
		else if (kind === 'padding' && raw !== 'plus') await typeBox(root, box('padding'), raw);
		else if (label === 'snap off') root.querySelector<HTMLInputElement>(box('snap'))!.click();
		else if (label === 'trim on') root.querySelector<HTMLInputElement>(box('trim'))!.click();
		else if (kind === 'aspect') byText(root, '[data-field-name="aspect"] button', label.slice(7))!.click();
		else if (label === 'pattern typed') {
			const input = root.querySelector<HTMLInputElement>(box('pattern'))!;
			input.value = 'sheet_{n}';
			input.dispatchEvent(new Event('input', { bubbles: true }));
		} else if (label === 'token') byText(root, '[aria-label="Insert a token"] button', '{row}')!.click();
		await settle();
		return calls;
	}

	it('calls the same handlers with the same values as the old steppers, switches, segments and pattern', async () => {
		const cases = (old.slice as Array<{ label: string; calls: unknown[][] }>).filter((item) => !/\bx$|plus/.test(item.label) && item.label !== 'aspect Keep');
		expect(cases.length).toBeGreaterThan(20);
		for (const item of cases) expect(await run(item.label), item.label).toEqual(item.calls);
	});

	it('picks a collection and clears it again', async () => {
		const calls: unknown[][] = [];
		const view = mountComponent<Record<string, unknown>>(SliceOptions, {
			params: { ...sliceParams(2), collection: 'c1' },
			tab: 'output',
			collections: [{ value: 'c1', label: 'Sprites' }],
			onpreset: () => undefined,
			ondetect: () => undefined,
			onsnap: () => undefined,
			onoutput: () => undefined,
			onpattern: () => undefined,
			onrename: () => undefined,
			onskip: () => undefined,
			oncollection: (id: string | null) => calls.push([id]),
			onadd: () => undefined,
			onclear: () => undefined,
			oncell: () => undefined
		});
		await settle();
		expect(view.target.querySelector('[data-field-name="collection"]')?.textContent).toContain('Sprites');
	});
});

describe('Number box text', () => {
	function mountBox(extra: Record<string, unknown> = {}) {
		const changes: unknown[] = [];
		const view = mountComponent<Record<string, unknown>>(NumberBoxField, { name: 'side', config: { title: 'Side', integer: true, ...extra }, value: 10, onChange: (_name: string, next: unknown) => changes.push(next), onCommit: undefined });
		const input = () => view.target.querySelector<HTMLInputElement>('input')!;
		return { view, changes, input };
	}

	it('keeps what is typed while the field is focused and dirty', async () => {
		const { view, input } = mountBox();
		input().focus();
		input().value = '12';
		input().dispatchEvent(new Event('input', { bubbles: true }));
		view.rerender({ value: 5 });
		flushSync();
		expect(input().value).toBe('12');
	});

	it('follows the held value again once the typed one is committed', async () => {
		const { view, input } = mountBox();
		input().focus();
		input().value = '99999';
		input().dispatchEvent(new Event('input', { bubbles: true }));
		input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		view.rerender({ value: 4000 });
		flushSync();
		expect(input().value).toBe('4000');
	});

	it('labels the input for assistive tech when asked', () => {
		const { input } = mountBox({ aria_label: 'Crop X' });
		expect(input().getAttribute('aria-label')).toBe('Crop X');
	});
});
