// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { settle } from './helpers/settle';
import { destroyMounted, mountComponent } from './helpers/mount';

const kitDetail = {
	id: 'kt_1',
	name: 'Mara',
	handle: 'mara',
	label: null,
	position: 0,
	item_count: 0,
	counts: {},
	preview_items: [],
	items: [],
	created_at: '',
	updated_at: ''
};

vi.mock('$lib/services/api/index', () => ({
	api: {
		getKit: vi.fn(),
		listKits: vi.fn(),
		addKitItems: vi.fn(),
		copyGenerationFileToLibrary: vi.fn(),
		getFileURL: vi.fn((id: string, size?: string) => `/api/media/files/${id}?size=${size}`)
	}
}));
vi.mock('$lib/components/modals/GenerationHistoryModal.svelte', async () => ({
	default: (await import('./fixtures/HistoryPickerStub.svelte')).default
}));
vi.mock('$lib/components/modals/UploadLibraryModal.svelte', async () => ({
	default: (await import('./fixtures/HistoryPickerStub.svelte')).default
}));
vi.mock('$lib/components/modals/MediaPreviewModal.svelte', async () => ({
	default: (await import('./fixtures/HistoryPickerStub.svelte')).default
}));

const { api } = await import('$lib/services/api/index');
const { default: KitDetailPane } = await import('$lib/components/kits/KitDetailPane.svelte');

async function mountPane() {
	const view = mountComponent(KitDetailPane as never, {
		kitId: 'kt_1',
		onBack: () => {},
		onDeleted: () => {},
		onOpenKit: () => {}
	});
	await settle();
	flushSync();
	return view;
}

beforeEach(() => {
	vi.mocked(api.getKit).mockResolvedValue({ success: true, data: kitDetail } as never);
	vi.mocked(api.listKits).mockResolvedValue({ success: true, data: { labels: [] } } as never);
	vi.mocked(api.addKitItems).mockResolvedValue({ success: true, data: kitDetail } as never);
});

afterEach(() => {
	destroyMounted();
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

const historyTileButton = () =>
	Array.from(document.body.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'History');

describe('adding History items to a kit', () => {
	it('offers History beside Library, Upload and Text on the empty tile', async () => {
		await mountPane();
		const labels = Array.from(document.body.querySelectorAll('button')).map((button) => button.textContent?.trim());
		expect(labels).toEqual(expect.arrayContaining(['Library', 'History', 'Upload', 'Text']));
	});

	it('opens the History picker from the tile and adds the picked files by file id, without copying to the Library', async () => {
		await mountPane();
		historyTileButton()!.click();
		flushSync();
		document.body.querySelector<HTMLButtonElement>('[data-stub-history-confirm]')!.click();
		await settle();

		expect(api.copyGenerationFileToLibrary).not.toHaveBeenCalled();
		expect(api.addKitItems).toHaveBeenCalledTimes(1);
		const [kitId, items] = vi.mocked(api.addKitItems).mock.calls[0];
		expect(kitId).toBe('kt_1');
		expect(items).toEqual([
			{ kind: 'image', name: 'image1', history_file_id: '7' },
			{ kind: 'audio', name: 'audio1', history_file_id: '8' }
		]);
	});
});
