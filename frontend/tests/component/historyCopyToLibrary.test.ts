// @vitest-environment jsdom
//
// "Copy to Library" reads the selection and the loaded generations straight off
// historyStore, so the thing worth proving is the wiring: which file ids reach
// the copy route, that history itself is left alone (it is a copy, not a move),
// and that the toolbar says so. Mounts the real component against the real
// stores.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		copyGenerationFileToLibrary: vi.fn(),
		listCollections: vi.fn(),
		getGenerationHistory: vi.fn(),
		deleteGenerationHistory: vi.fn(),
		bulkDeleteGenerations: vi.fn()
	}
}));

const { api } = await import('$lib/services/api/index');
const { historyStore } = await import('$lib/stores/history');
const { default: HistorySelectionToolbar } = await import(
	'../../src/routes/history/components/HistorySelectionToolbar.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

const GENERATIONS = [
	{
		id: 'gen-1',
		status: 'completed',
		files: [
			{ id: 11, file_path: 'a.png', file_type: 'image', is_final: true },
			{ id: 12, file_path: 'a-step.png', file_type: 'image', is_final: false }
		]
	},
	{
		id: 'gen-2',
		status: 'completed',
		files: [{ id: 21, file_path: 'b.mp4', file_type: 'video', is_final: true }]
	}
];

function mountToolbar() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: HistorySelectionToolbar as never,
		target,
		props: {
			onBulkDeleteClick: () => {},
			onCompareClick: () => {}
		}
	});
	return {
		target,
		copyButton: () =>
			Array.from(target.querySelectorAll('button')).find((b) =>
				b.textContent?.includes('Copy to Library')
			),
		text: () => target.textContent ?? '',
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function currentHistoryState() {
	let state: any;
	historyStore.subscribe((s) => (state = s))();
	return state;
}

let mounted: ReturnType<typeof mountToolbar> | undefined;

beforeEach(async () => {
	historyStore.clearSelection();
	vi.mocked(api.listCollections).mockResolvedValue({
		success: true,
		data: { collections: [], total: 0 }
	} as never);
	vi.mocked(api.getGenerationHistory).mockResolvedValue({
		success: true,
		data: { generations: GENERATIONS, total: GENERATIONS.length }
	} as never);
	vi.mocked(api.copyGenerationFileToLibrary).mockResolvedValue({ success: true } as never);
	await historyStore.loadGenerations();
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	historyStore.clearSelection();
	vi.clearAllMocks();
});

describe('history selection toolbar - copy to library', () => {
	it('copies every final media file of the selected generations, by file id', async () => {
		historyStore.toggleSelect('gen-1');
		historyStore.toggleSelect('gen-2');

		mounted = mountToolbar();
		await settle();

		mounted.copyButton()!.click();
		await settle();

		expect(api.copyGenerationFileToLibrary).toHaveBeenCalledTimes(2);
		expect(api.copyGenerationFileToLibrary).toHaveBeenCalledWith('11');
		expect(api.copyGenerationFileToLibrary).toHaveBeenCalledWith('21');
		// The intermediate file is not the user's output and never gets copied.
		expect(api.copyGenerationFileToLibrary).not.toHaveBeenCalledWith('12');
	});

	it('only copies what is selected', async () => {
		historyStore.toggleSelect('gen-2');

		mounted = mountToolbar();
		await settle();

		mounted.copyButton()!.click();
		await settle();

		expect(api.copyGenerationFileToLibrary).toHaveBeenCalledTimes(1);
		expect(api.copyGenerationFileToLibrary).toHaveBeenCalledWith('21');
	});

	it('leaves history untouched - it is a copy, not a move', async () => {
		historyStore.toggleSelect('gen-1');

		mounted = mountToolbar();
		await settle();

		mounted.copyButton()!.click();
		await settle();

		expect(api.deleteGenerationHistory).not.toHaveBeenCalled();
		expect(api.bulkDeleteGenerations).not.toHaveBeenCalled();
		expect(currentHistoryState().generations).toHaveLength(2);
	});

	// Clearing the selection would unmount the whole bar, taking the only
	// confirmation the user gets with it.
	it('reports the copy in the toolbar, keeping the selection so the message is visible', async () => {
		historyStore.toggleSelect('gen-1');

		mounted = mountToolbar();
		await settle();

		mounted.copyButton()!.click();
		await settle();

		expect(mounted.text()).toContain('Copied 1 file to Library');
		expect(mounted.text()).not.toMatch(/moved/i);
		expect(currentHistoryState().selectedGenerationIds).toEqual(['gen-1']);
	});

	it('keeps the selection and reports the failure when the copy fails', async () => {
		vi.mocked(api.copyGenerationFileToLibrary).mockResolvedValue({ success: false } as never);
		historyStore.toggleSelect('gen-1');

		mounted = mountToolbar();
		await settle();

		mounted.copyButton()!.click();
		await settle();

		expect(mounted.text()).toContain('Failed to copy 1 file to Library');
		expect(currentHistoryState().selectedGenerationIds).toEqual(['gen-1']);
	});
});
