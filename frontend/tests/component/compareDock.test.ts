// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';
import type { GenerationState } from '$lib/types/tabs';

vi.mock('$lib/generation/compare/compareApi', () => ({
	postGrid: vi.fn(),
	fetchGrid: vi.fn(),
	postRetryFailed: vi.fn(),
	removeGrid: vi.fn(),
	fetchGridSettings: vi.fn(async () => ({ confirm_above: 24, hard_cap: 100 }))
}));

const { default: GenerationPanel } = await import('../../src/lib/components/GenerationPanel.svelte');
const { createClassComponent, flushSync } = await import('svelte/legacy').then(async (legacy) => ({
	createClassComponent: legacy.createClassComponent,
	flushSync: (await import('svelte')).flushSync
}));
const { tabsStore } = await import('$lib/stores/tabs');
const store = await import('$lib/generation/compare/compareStore.svelte');

function generation(overrides: Partial<GenerationState> = {}): GenerationState {
	return {
		isGenerating: false,
		currentGeneration: null,
		currentProgress: null,
		pipeTimers: {},
		startedAt: null,
		totalTime: null,
		lastDurationMs: null,
		batchImages: [],
		batchVideos: [],
		batchAudios: [],
		artifacts: [],
		workbenchIndex: 0,
		workbenchTotal: 0,
		queue: [],
		submittedPromptTemplate: null,
		...overrides
	};
}

const axis = (field: string, values: string[]) => ({
	field,
	type: 'select',
	label: field,
	values: values.map((value) => ({ value, label: value }))
});

let tabId = '';
let mounted: { target: HTMLElement; destroy: () => void } | undefined;

function mount(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: GenerationPanel as never,
		target,
		props: { generation: generation(), isGenerating: false, canGenerate: true, presetId: 'native/Krea2', tabId, ...props }
	});
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 4; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

function compareButton(root: HTMLElement) {
	return root.querySelector<HTMLButtonElement>('[data-compare-toggle]')!;
}

function continuousButton(root: HTMLElement) {
	return Array.from(root.querySelectorAll<HTMLButtonElement>('.mode-button')).find(
		(button) => !button.hasAttribute('data-compare-toggle')
	)!;
}

beforeEach(() => {
	tabsStore.reset();
	store.resetCompareStoreForTests();
	tabId = get(tabsStore).tabs[0].id;
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('dock Compare button', () => {
	it('sits next to the Continuous button and arms Compare with the signal styling', async () => {
		mounted = mount();
		const button = compareButton(mounted.target);
		expect(button).toBeTruthy();
		expect(button.closest('.run-cluster')).toBe(continuousButton(mounted.target).closest('.run-cluster'));
		expect(button.closest('.run-cluster')).not.toBeNull();
		expect(button.classList.contains('is-continuous')).toBe(false);

		button.click();
		await settle();

		expect(store.getCompare(tabId).armed).toBe(true);
		expect(compareButton(mounted.target).classList.contains('is-continuous')).toBe(true);
		expect(compareButton(mounted.target).getAttribute('aria-pressed')).toBe('true');
	});

	it('disables Continuous with a reason while Compare is armed', async () => {
		store.setCompare(tabId, { armed: true });
		mounted = mount();
		await settle();
		const continuous = continuousButton(mounted.target);
		expect(continuous.disabled).toBe(true);
		expect(continuous.getAttribute('aria-label')).toContain("can't run while Compare is on");
	});

	it('disables Compare while Continuous is on, saying why', async () => {
		mounted = mount();
		await settle();
		continuousButton(mounted.target).click();
		await settle();
		const compare = compareButton(mounted.target);
		expect(compare.disabled).toBe(true);
		expect(compare.getAttribute('aria-label')).toContain("can't run while Continuous is on");
	});

	it('is disabled with the block reason for a preset using the Video Director', async () => {
		store.setCompareBlocked(tabId, "Compare isn't available while the Video Director is on.");
		mounted = mount();
		await settle();
		const compare = compareButton(mounted.target);
		expect(compare.disabled).toBe(true);
		expect(compare.getAttribute('aria-label')).toContain('Video Director');
	});
});

describe('dock Generate with Compare armed', () => {
	it('reads Generate 12 and shows the cell count status line', async () => {
		store.setCompare(tabId, { armed: true, x: axis('sampler', ['a', 'b', 'c', 'd']), y: axis('scheduler', ['x', 'y', 'z']) });
		mounted = mount();
		await settle();
		const generate = mounted.target.querySelector<HTMLButtonElement>('.generate-button')!;
		expect(generate.textContent).toContain('Generate');
		expect(generate.querySelector('.generate-count')?.textContent).toBe('12');
		expect(mounted.target.querySelector('[data-compare-status]')?.textContent?.trim()).toMatch(/^12 cells · -$/);
		expect(generate.disabled).toBe(false);
	});

	it('disables Generate when nothing is on an axis yet', async () => {
		store.setCompare(tabId, { armed: true });
		mounted = mount();
		await settle();
		expect(mounted.target.querySelector<HTMLButtonElement>('.generate-button')!.disabled).toBe(true);
	});

	it('turns into Cancel all while the grid runs', async () => {
		store.setCompare(tabId, { armed: true, x: axis('sampler', ['a', 'b']) });
		const { postGrid } = await import('$lib/generation/compare/compareApi');
		vi.mocked(postGrid).mockResolvedValueOnce({
			id: 'g1',
			preset_id: 'native/Krea2',
			tab_id: tabId,
			x_axis: axis('sampler', ['a', 'b']),
			y_axis: null,
			lock_seed: true,
			status: 'running',
			created_at: '2026-10-05T10:00:00Z',
			cells: [0, 1].map((x) => ({
				x,
				y: 0,
				generation_id: `g-${x}`,
				status: 'queued' as const,
				axis_values: {},
				seed: 1,
				thumbnail_url: null,
				media_type: null,
				error: null
			}))
		});
		await store.submitGrid(tabId, {} as never);
		mounted = mount({ isGenerating: true });
		await settle();
		const mark = mounted.target.querySelector<HTMLButtonElement>('.generate-button')!;
		expect(mark.classList.contains('is-cancel')).toBe(true);
		expect(mark.textContent).toContain('Cancel all');
	});

	it('shows the Plans shortfall as the disabled reason', async () => {
		store.setCompare(tabId, { armed: true, x: axis('sampler', ['a', 'b', 'c', 'd']), y: axis('scheduler', ['x', 'y', 'z']) });
		const plans = await import('$lib/plans/store');
		plans.limits.set([
			{ kind: 'generations_per_day', label: 'Generations today', used: 15, limit: 20, format: 'count', resets_at: null, state: 'warn', percent: 75, enforced: true }
		]);
		mounted = mount();
		await settle();
		expect(mounted.target.querySelector<HTMLButtonElement>('.generate-button')!.disabled).toBe(true);
		expect(mounted.target.textContent).toContain('12 needed, 5 left today');
		plans.limits.set([]);
	});
});
