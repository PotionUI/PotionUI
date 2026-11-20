// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
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
const { setModeLabels } = await import('$lib/utils/modeLabel.svelte');
const { default: HistoryToolbar } = await import('../../src/routes/history/components/HistoryToolbar.svelte');
const { createClassComponent } = await import('svelte/legacy');

function byText(selector: string, text: string) {
	return Array.from(document.querySelectorAll<HTMLElement>(selector)).find((e) => e.textContent?.includes(text));
}

describe('History filters show plain mode names', () => {
	let destroy: () => void;

	beforeEach(async () => {
		setModeLabels({ txt2img: 'Text to Image', img2video: 'Image to Video' });
		vi.mocked(api.getGenerationHistory).mockResolvedValue({ success: true, data: { generations: [], total: 0 } } as never);
		vi.mocked(api.getHistoryFacets).mockResolvedValue({
			success: true,
			data: {
				modes: [
					{ value: 'txt2img', count: 4 },
					{ value: 'img2video', count: 2 },
					{ value: 'face_swap', count: 1 }
				],
				presets: [],
				models: []
			}
		} as never);
		historyStore.setFilter('mode', undefined);
		await historyStore.loadFacets();
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = createClassComponent({
			component: HistoryToolbar as never,
			target,
			props: { onOpenUpload: () => {}, onOpenAddTag: () => {}, onOpenDeleteByCriteria: () => {} }
		});
		destroy = () => {
			component.$destroy();
			target.remove();
		};
	});

	afterEach(() => {
		destroy();
		historyStore.setFilter('mode', undefined);
		document.body.innerHTML = '';
	});

	it('labels the mode filter options and keeps the keys as values', async () => {
		byText('button', 'Filters')!.click();
		await settle();
		document.querySelector<HTMLButtonElement>('button[aria-label="Mode"]')!.click();
		await settle();
		const options = Array.from(document.querySelectorAll<HTMLElement>('[role="listbox"][aria-label="Mode"] [role="option"]')).slice(1);
		expect(options.map((o) => o.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
			'Text to Image 4',
			'Image to Video 2',
			'Face Swap 1'
		]);
		options[1].click();
		await settle();
		expect(get(historyStore).filters.mode).toBe('img2video');
	});

	it('shows the plain name on the active mode chip', async () => {
		historyStore.setFilter('mode', 'img2video');
		await settle();
		byText('button', 'Filters')!.click();
		await settle();
		const chip = document.querySelector<HTMLElement>('[aria-label="Remove Image to Video"]')!;
		expect(chip.parentElement?.textContent?.trim()).toBe('Image to Video');
	});
});
