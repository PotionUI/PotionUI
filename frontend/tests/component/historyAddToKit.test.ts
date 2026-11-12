// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { settle } from './helpers/settle';
import { destroyMounted, mountComponent } from './helpers/mount';

vi.mock('$lib/services/api/index', () => ({
	api: {
		listCollections: vi.fn(),
		getGenerationHistory: vi.fn(),
		getFileURL: vi.fn((id: string, size?: string) => `/api/media/files/${id}?size=${size}`)
	}
}));
const toastError = vi.fn();
vi.mock('$lib/stores/toast', () => ({
	toasts: { success: vi.fn(), error: (...args: unknown[]) => toastError(...args), info: vi.fn(), warning: vi.fn(), show: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { historyStore } = await import('$lib/stores/history');
const { kitAddRequest, requestAddToKit, requestAddGenerationsToKit } = await import('$lib/stores/kitAddRequests');
const { get } = await import('svelte/store');
const { default: HistorySelectionToolbar } = await import('../../src/routes/history/components/HistorySelectionToolbar.svelte');

function generation(id: string, files: Array<Record<string, unknown>>) {
	return { id, status: 'completed', files, form_data: {}, created_at: '' };
}

const file = (id: number, file_type: string, extra: Record<string, unknown> = {}) => ({
	id,
	file_path: `out/${id}.${file_type === 'image' ? 'png' : 'bin'}`,
	file_type,
	is_final: true,
	...extra
});

beforeEach(() => {
	kitAddRequest.set(null);
	toastError.mockReset();
});

afterEach(() => {
	destroyMounted();
	historyStore.clearSelection();
	document.body.innerHTML = '';
});

describe('requestAddToKit', () => {
	it('turns generated files into history picks the compose dialog can add', () => {
		expect(requestAddToKit([file(7, 'image'), file(8, 'video')])).toBe(true);
		const picks = get(kitAddRequest)!;
		expect(picks.map((pick) => [pick.kind, pick.historyFileId])).toEqual([
			['image', '7'],
			['video', '8']
		]);
		expect(picks[0].thumbnail).toBe('/api/media/files/7?size=small');
	});

	it('tells the user when nothing in the output fits a kit, and opens nothing', () => {
		expect(requestAddToKit([file(9, 'mesh')])).toBe(false);
		expect(get(kitAddRequest)).toBeNull();
		expect(toastError).toHaveBeenCalledWith('Kits hold pictures, clips, sounds and notes.');
	});

	it('leaves out intermediate files of a generation', () => {
		requestAddGenerationsToKit([generation('g', [file(1, 'image'), file(2, 'image', { is_final: false })])] as never);
		expect(get(kitAddRequest)!.map((pick) => pick.historyFileId)).toEqual(['1']);
	});
});

describe('History selection bar', () => {
	function mountBar() {
		return mountComponent(HistorySelectionToolbar as never, { onBulkDeleteClick: () => {}, onToolSelect: () => {} });
	}
	const addButton = () => document.body.querySelector<HTMLButtonElement>('[data-add-to-kit]');

	beforeEach(() => {
		historyStore.clearSelection();
	});

	it('shows Add to kit only once something is selected', async () => {
		historyStore.toggleSelectionMode();
		mountBar();
		await settle();
		flushSync();
		expect(addButton()).toBeNull();
	});

	it('sends the selected generations files to the kit dialog', async () => {
		const generations = [
			generation('g1', [file(11, 'image')]),
			generation('g2', [file(12, 'video')]),
			generation('g3', [file(13, 'image')])
		];
		vi.mocked(api.getGenerationHistory).mockResolvedValue({
			success: true,
			data: { generations, total: 3 }
		} as never);
		await historyStore.loadGenerations();
		historyStore.setSelection(['g1', 'g2']);
		mountBar();
		await settle();
		flushSync();
		expect(addButton()).not.toBeNull();
		addButton()!.click();
		flushSync();
		expect(get(kitAddRequest)!.map((pick) => pick.historyFileId)).toEqual(['11', '12']);
	});
});
