// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { settle } from './helpers/settle';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getModels: vi.fn(),
		getModelTypes: vi.fn(),
		getGenerationHistory: vi.fn(),
		getHistoryFacets: vi.fn(),
		searchPhrasebook: vi.fn(),
		setOnAuthExpired: vi.fn(),
		setOnTokenRefreshed: vi.fn(),
		listPresets: vi.fn().mockResolvedValue({ success: true, data: [] })
	}
}));

const { api } = await import('$lib/services/api/index');
const { historyStore } = await import('$lib/stores/history');
const { default: HistoryToolbar } = await import(
	'../../src/routes/history/components/HistoryToolbar.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function currentFilters() {
	let state: any;
	historyStore.subscribe((s) => (state = s))();
	return state.filters;
}

function mount() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: HistoryToolbar as never,
		target,
		props: { onOpenUpload: () => {}, onOpenAddTag: () => {}, onOpenDeleteByCriteria: () => {} }
	});
	return () => {
		component.$destroy();
		target.remove();
	};
}

function byText(selector: string, text: string) {
	return Array.from(document.querySelectorAll<HTMLElement>(selector)).find((e) =>
		e.textContent?.includes(text)
	);
}

describe('History filters model picker', () => {
	let destroy: () => void;

	beforeEach(() => {
		vi.mocked(api.getModels).mockResolvedValue({
			success: true,
			data: {
				models: [{ id: 'm1', filename: 'dreamshaper.safetensors', name: 'Dream Shaper', model_type: 'checkpoint', files: [] }],
				total: 1
			}
		} as never);
		vi.mocked(api.getModelTypes).mockResolvedValue({ success: true, data: { types: [] } } as never);
		vi.mocked(api.getGenerationHistory).mockResolvedValue({
			success: true,
			data: { generations: [], total: 0 }
		} as never);
		vi.mocked(api.getHistoryFacets).mockResolvedValue({
			success: true,
			data: {
				modes: [],
				presets: [],
				models: [{ name: 'tuned_v2_fp8.safetensors', label: 'Tuned Model v2', count: 4 }]
			}
		} as never);
		historyStore.setFilter('modelName', undefined);
		destroy = mount();
	});

	afterEach(() => {
		destroy();
		document.body.innerHTML = '';
	});

	async function openPicker() {
		byText('button', 'Filters')!.click();
		await settle();
		document.getElementById('history-filter-model')!.click();
		await settle();
	}

	it('opens the shared model modal from the filter, with no select element', async () => {
		byText('button', 'Filters')!.click();
		await settle();
		expect(document.querySelector('select#history-filter-model')).toBeNull();
		document.getElementById('history-filter-model')!.click();
		await settle();
		expect(document.body.textContent).toContain('Filter history by model');
		expect(api.getModels).toHaveBeenCalled();
	});

	it('picking a model sets the filter and shows a removable chip', async () => {
		await openPicker();
		const card = Array.from(document.querySelectorAll<HTMLElement>('.cursor-pointer[role="button"]')).find((e) =>
			e.textContent?.includes('dreamshaper')
		)!;
		card.click();
		await settle();
		expect(currentFilters().modelName).toBe('dreamshaper.safetensors');
		expect(document.body.textContent).not.toContain('Filter history by model');
		const chip = document.querySelector<HTMLElement>('[aria-label="Clear model filter"]')!;
		expect(chip.textContent).toContain('Dream Shaper');
		expect(chip.textContent).not.toContain('.safetensors');
		chip.click();
		await settle();
		expect(currentFilters().modelName).toBeUndefined();
		expect(document.querySelector('[aria-label="Clear model filter"]')).toBeNull();
	});

	it('shows the facet display name, or a cleaned stem, never the file name', async () => {
		historyStore.setFilter('modelName', 'sub/flux_fp8_scaled.safetensors');
		byText('button', 'Filters')!.click();
		await settle();
		const trigger = document.getElementById('history-filter-model')!;
		expect(trigger.textContent).toContain('flux_fp8_scaled');
		expect(trigger.textContent).not.toContain('.safetensors');
		expect(trigger.textContent).not.toContain('sub/');
	});

	it('prefers the display name from the history facets for a filter set by file name', async () => {
		await historyStore.loadFacets();
		historyStore.setFilter('modelName', 'tuned_v2_fp8.safetensors');
		byText('button', 'Filters')!.click();
		await settle();
		expect(document.getElementById('history-filter-model')!.textContent).toContain('Tuned Model v2');
		expect(document.querySelector('[aria-label="Clear model filter"]')!.textContent).toContain('Tuned Model v2');
		expect(currentFilters().modelName).toBe('tuned_v2_fp8.safetensors');
	});

	it('All models clears an existing pick', async () => {
		historyStore.setFilter('modelName', 'old.safetensors');
		await openPicker();
		byText('button', 'All models')!.click();
		await settle();
		expect(currentFilters().modelName).toBeUndefined();
	});

	it('the model filter control clears in one click without opening the modal', async () => {
		historyStore.setFilter('modelName', 'old.safetensors');
		byText('button', 'Filters')!.click();
		await settle();
		const clear = document.querySelector<HTMLElement>('#history-filter-model ~ [aria-label="Clear model"]')!;
		expect(clear).not.toBeNull();
		clear.click();
		await settle();
		expect(currentFilters().modelName).toBeUndefined();
		expect(document.body.textContent).not.toContain('Filter history by model');
		expect(document.querySelector('#history-filter-model ~ [aria-label="Clear model"]')).toBeNull();
	});
});
