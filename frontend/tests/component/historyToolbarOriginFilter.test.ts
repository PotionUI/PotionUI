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

describe('History filters origin', () => {
	let destroy: () => void;

	beforeEach(() => {
		vi.mocked(api.getModels).mockResolvedValue({ success: true, data: { models: [], total: 0 } } as never);
		vi.mocked(api.getModelTypes).mockResolvedValue({ success: true, data: { types: [] } } as never);
		vi.mocked(api.getGenerationHistory).mockResolvedValue({
			success: true,
			data: { generations: [], total: 0 }
		} as never);
		vi.mocked(api.getHistoryFacets).mockResolvedValue({
			success: true,
			data: { modes: [], presets: [], models: [] }
		} as never);
		historyStore.clearFilters();
		destroy = mount();
	});

	afterEach(() => {
		destroy();
		document.body.innerHTML = '';
	});

	it('picks an origin, shows a removable chip, and clears with Clear all', async () => {
		byText('button', 'Filters')!.click();
		await settle();
		document.querySelector<HTMLElement>('[aria-label="Origin"]')!.click();
		await settle();
		byText('[role="option"], [role="menuitem"], button', 'Made in the editor')!.click();
		await settle();
		expect(currentFilters().origin).toBe('editor');
		expect(vi.mocked(api.getGenerationHistory).mock.calls.at(-1)?.[0]).toMatchObject({ origin: 'editor' });
		expect(document.querySelector('[aria-label="Remove Made in the editor"]')).not.toBeNull();
		byText('button', 'Clear all')!.click();
		await settle();
		expect(currentFilters().origin).toBeUndefined();
		expect(document.querySelector('[aria-label="Remove Made in the editor"]')).toBeNull();
	});
});
