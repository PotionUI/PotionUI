// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { get } from 'svelte/store';

vi.mock('$lib/generation/compare/compareApi', () => ({
	fetchGridSettings: vi.fn().mockResolvedValue({ confirm_above: 24, hard_cap: 100 }),
	postGrid: vi.fn(),
	fetchGrid: vi.fn(),
	postRetryFailed: vi.fn(),
	removeGrid: vi.fn()
}));

vi.mock('$lib/plans/meApi', async (original) => ({
	...(await original<typeof import('$lib/plans/meApi')>()),
	getMyLimits: vi.fn().mockRejectedValue(new Error('offline'))
}));

const { default: CompareDrawer } = await import('$lib/components/compare/CompareDrawer.svelte');
const store = await import('$lib/generation/compare/compareStore.svelte');
const { tabsStore } = await import('$lib/stores/tabs');
const { limits } = await import('$lib/plans/store');
const { viewportWidth } = await import('$lib/stores/viewport');

const schema = {
	properties: {
		root: {
			type: 'tabs',
			children: [
				{
					type: 'tab',
					label: 'Generation',
					children: [
						{
							type: 'section',
							label: 'Sampling',
							children: [
								{
									name: 'sampler',
									type: 'select',
									title: 'Sampler',
									options: ['euler', 'er_sde', 'dpmpp_2m', 'dpmpp_2m_sde', 'heun', 'uni_pc'].map((v) => ({
										value: v,
										label: v
									}))
								},
								{
									name: 'scheduler',
									type: 'select',
									title: 'Scheduler',
									options: ['simple', 'beta', 'karras', 'normal', 'sgm_uniform'].map((v) => ({
										value: v,
										label: v
									}))
								}
							]
						}
					]
				}
			]
		}
	}
};

function axis(field: string, label: string, values: string[]) {
	return { field, type: 'select', label, values: values.map((v) => ({ value: v, label: v })) };
}

let tabId: string;
let instance: ReturnType<typeof mount> | undefined;
let onGenerate: ReturnType<typeof vi.fn<() => void>>;

function configure() {
	store.setCompare(tabId, { armed: true });
	store.setAxis(tabId, 'x', axis('sampler', 'Sampler', ['euler', 'er_sde', 'dpmpp_2m', 'dpmpp_2m_sde']));
	store.setAxis(tabId, 'y', axis('scheduler', 'Scheduler', ['simple', 'beta', 'karras']));
}

function mountDrawer(props: Record<string, unknown> = {}) {
	instance = mount(CompareDrawer, {
		target: document.body,
		props: { tabId, canGenerate: true, isGenerating: false, onGenerate, ...props }
	});
	flushSync();
}

function buttonByText(text: string): HTMLButtonElement | undefined {
	return [...document.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent?.trim() === text);
}

beforeEach(() => {
	store.resetCompareStoreForTests();
	viewportWidth.set(1280);
	limits.set([]);
	onGenerate = vi.fn<() => void>();
	tabId = tabsStore.addTabWithData('Compare test', {
		selectedPreset: 'krea2',
		selectedMode: 'txt2img',
		formData: { seed: 4211984, quantity: 1, sampler: 'euler', scheduler: 'simple' }
	});
	store.publishCompareSchema(tabId, 'krea2-txt2img', schema);
	configure();
	store.openCompareDrawer(tabId);
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
	tabsStore.removeTab(tabId);
});

describe('CompareDrawer', () => {
	it('shows the cell summary for the configured axes', () => {
		mountDrawer();
		expect(document.querySelector('[data-testid="compare-summary"]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
			'4 × 3 = 12 generations'
		);
		expect(document.querySelector('[data-testid="axis-count-x"]')?.textContent).toBe('4 of 6');
		expect(document.querySelector('[data-testid="axis-count-y"]')?.textContent).toBe('3 of 5');
	});

	it('says quantity counts as 1 per cell when the field the preset reads as quantity is above 1', () => {
		store.publishCompareSchema(tabId, 'krea2-txt2img', { ...schema, quantity_fields: ['count'] });
		tabsStore.updateTab(tabId, { formData: { seed: 4211984, quantity: 5, count: 1, sampler: 'euler', scheduler: 'simple' } });
		mountDrawer();
		expect(document.body.textContent).not.toContain('Quantity counts as 1 per cell');

		tabsStore.updateTab(tabId, { formData: { seed: 4211984, quantity: 1, count: 3, sampler: 'euler', scheduler: 'simple' } });
		flushSync();
		expect(document.body.textContent).toContain('Quantity counts as 1 per cell');
	});

	it('swaps the axes', () => {
		mountDrawer();
		buttonByText('Swap X and Y')!.click();
		flushSync();
		const config = store.getCompare(tabId);
		expect(config.x?.field).toBe('scheduler');
		expect(config.y?.field).toBe('sampler');
	});

	it('clears both axes', () => {
		mountDrawer();
		buttonByText('Clear')!.click();
		flushSync();
		const config = store.getCompare(tabId);
		expect(config.x).toBeNull();
		expect(config.y).toBeNull();
		expect(config.armed).toBe(true);
	});

	it('turns Compare off and closes the drawer', () => {
		mountDrawer();
		buttonByText('Turn off')!.click();
		flushSync();
		expect(store.getCompare(tabId).armed).toBe(false);
		expect(store.isCompareDrawerOpen(tabId)).toBe(false);
		expect(document.querySelector('[role="dialog"]')).toBeNull();
	});

	it('shows the plans shortfall and has no Generate button on desktop', () => {
		limits.set([
			{
				kind: 'generations_per_day',
				label: 'Generations today',
				used: 95,
				limit: 100,
				format: 'count',
				resets_at: null,
				state: 'warn',
				percent: 95,
				enforced: true
			}
		]);
		mountDrawer();
		expect(document.querySelector('[data-testid="compare-shortfall"]')?.textContent).toContain('12 needed, 5 left today');
		expect(document.body.textContent).toContain('Ask your admin for more.');
		expect(buttonByText('Generate 12')).toBeUndefined();
	});

	it('forces Lock seed off and disabled when seed is an axis', () => {
		store.setAxis(tabId, 'y', { field: 'seed', type: 'seed', label: 'Seed', values: [{ value: 1, label: '1' }] });
		mountDrawer();
		const toggle = document.querySelector<HTMLInputElement>('[role="switch"][aria-label="Lock seed"]')!;
		expect(toggle.disabled).toBe(true);
		expect(toggle.checked).toBe(false);
		expect(document.body.textContent).toContain('Lock seed is off while seed is an axis.');
	});

	it('renders a Generate N sheet on mobile that is disabled by the plans shortfall', () => {
		viewportWidth.set(390);
		limits.set([
			{
				kind: 'generations_per_day',
				label: 'Generations today',
				used: 95,
				limit: 100,
				format: 'count',
				resets_at: null,
				state: 'warn',
				percent: 95,
				enforced: true
			}
		]);
		mountDrawer();
		const generate = buttonByText('Generate 12')!;
		expect(generate.disabled).toBe(true);
		expect(get(limits)).toHaveLength(1);
	});

	it('runs Generate from the mobile sheet', () => {
		viewportWidth.set(390);
		mountDrawer();
		const generate = buttonByText('Generate 12')!;
		expect(generate.disabled).toBe(false);
		generate.click();
		expect(onGenerate).toHaveBeenCalledTimes(1);
	});
});
