import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationById: vi.fn(),
		cancelGeneration: vi.fn(),
		deleteGenerationHistory: vi.fn(),
		getBaseURL: () => 'http://localhost',
		getToken: () => null,
		setOnAuthExpired: vi.fn(),
		setOnTokenRefreshed: vi.fn(),
		getClient: () => ({ get: vi.fn(), post: vi.fn() })
	}
}));

const { api } = await import('$lib/services/api/index');
const { tabsStore } = await import('$lib/stores/tabs');
const { toasts } = await import('$lib/stores/toast');
const { generationTabs } = await import('$lib/types/tabs');
const { resetLayerStackForTests } = await import('$lib/actions/layerStack');
const { default: GenerationDeleteDialog } = await import('../../src/routes/generate/components/GenerationDeleteDialog.svelte');
const { mountComponent } = await import('./helpers/mount');
const { settle } = await import('./helpers/settle');

const IMAGE = { url: '/api/media/generations/gen-1/1.png', originalUrl: '/api/media/generations/gen-1/1.png' };

function tab() {
	return generationTabs(get(tabsStore).tabs)[0];
}

function historyItem(fileCount: number) {
	return {
		id: 'gen-1',
		status: 'completed',
		files: Array.from({ length: fileCount }, (_, index) => ({ id: index, file_path: `out/${index}.png`, file_type: 'image' }))
	};
}

function seed(running: boolean) {
	const current = tab();
	tabsStore.updateTab(current.id, {
		activeGenerationId: running ? 'gen-1' : null,
		generation: {
			...current.generation,
			isGenerating: running,
			currentGeneration: { id: 'gen-1', status: running ? 'running' : 'completed' },
			batchImages: [IMAGE, IMAGE, IMAGE],
			workbenchTotal: 3,
			queue: running ? [{ generation_id: 'gen-1', queue_position: null, status: 'running' }] : []
		}
	});
}

async function open() {
	const props = { target: { tabId: tab().id, generationId: 'gen-1' }, unsubscribe: vi.fn(), onDeleted: vi.fn(), onClose: vi.fn() };
	mountComponent(GenerationDeleteDialog as never, props);
	await settle();
	return props;
}

const dialog = () => document.querySelector<HTMLElement>('[role="alertdialog"]');
const confirmButton = () =>
	Array.from(document.querySelectorAll<HTMLButtonElement>('[role="alertdialog"] button')).find((b) =>
		/^Delete/.test(b.textContent?.trim() ?? '')
	)!;

beforeEach(() => {
	tabsStore.reset();
	resetLayerStackForTests();
	vi.mocked(api.getGenerationById).mockResolvedValue({ success: true, data: historyItem(3) });
	vi.mocked(api.cancelGeneration).mockResolvedValue({ success: true, data: { cancel_notice: null } });
	vi.mocked(api.deleteGenerationHistory).mockResolvedValue({ success: true, data: { message: 'ok' } });
});

afterEach(() => {
	document.body.innerHTML = '';
	vi.clearAllMocks();
	vi.restoreAllMocks();
});

describe('deleting the tab generation from the generate page', () => {
	it('uses the History delete dialog with the whole generation file count', async () => {
		seed(false);
		await open();
		expect(api.getGenerationById).toHaveBeenCalledWith('gen-1', false, true);
		expect(dialog()!.textContent).toContain('Delete Generation');
		expect(dialog()!.textContent).toContain('This will permanently delete 3 generated file(s) from your disk.');
		expect(api.deleteGenerationHistory).not.toHaveBeenCalled();
	});

	it('deletes on confirm and leaves the tab workbench empty', async () => {
		seed(false);
		const props = await open();
		confirmButton().click();
		await settle();
		expect(api.cancelGeneration).not.toHaveBeenCalled();
		expect(api.deleteGenerationHistory).toHaveBeenCalledWith('gen-1');
		expect(props.onDeleted).toHaveBeenCalledWith({ tabId: tab().id, generationId: 'gen-1' });
		expect(tab().generation.currentGeneration).toBeNull();
		expect(tab().generation.batchImages).toEqual([]);
	});

	it('cancels a running generation behind the same single confirm, then deletes it', async () => {
		seed(true);
		const order: string[] = [];
		vi.mocked(api.cancelGeneration).mockImplementation(async () => {
			order.push('cancel');
			return { success: true, data: { cancel_notice: null } };
		});
		vi.mocked(api.deleteGenerationHistory).mockImplementation(async () => {
			order.push('delete');
			return { success: true, data: { message: 'ok' } };
		});
		await open();
		expect(order).toEqual([]);
		confirmButton().click();
		await settle();
		expect(order).toEqual(['cancel', 'delete']);
		expect(tab().activeGenerationId).toBeNull();
		expect(tab().generation.isGenerating).toBe(false);
		expect(tab().generation.batchImages).toEqual([]);
	});

	it('shows the blocked message with Open brew when a brew still holds the files, and keeps the result', async () => {
		seed(false);
		const show = vi.spyOn(toasts, 'show');
		vi.mocked(api.deleteGenerationHistory).mockRejectedValue(
			Object.assign(new Error('Request failed'), {
				response: {
					data: {
						detail: {
							message: 'A brew is still using this generation.',
							blocking_runs: [{ elixir_id: 'elx', elixir_name: 'Brew', run_id: 'run', number: 2 }]
						}
					}
				}
			})
		);
		const props = await open();
		confirmButton().click();
		await settle();
		expect(show).toHaveBeenCalledWith(
			'error',
			expect.any(String),
			expect.objectContaining({ title: 'Can’t delete yet', action: expect.objectContaining({ label: 'Open brew' }) })
		);
		expect(props.onDeleted).not.toHaveBeenCalled();
		expect(tab().generation.batchImages).toHaveLength(3);
		expect(dialog()).not.toBeNull();
	});

	it('closes without a dialog when the generation cannot be found', async () => {
		seed(false);
		vi.mocked(api.getGenerationById).mockResolvedValue({ success: false, message: 'Generation not found' });
		const props = await open();
		expect(dialog()).toBeNull();
		expect(props.onClose).toHaveBeenCalledTimes(1);
		expect(api.deleteGenerationHistory).not.toHaveBeenCalled();
	});
});
