// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('$lib/components/plugins/PluginSlot.svelte', async () => ({
	default: (await import('./fixtures/PluginSlotStub.svelte')).default
}));
import { settle } from './helpers/settle';

class StubResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}
vi.stubGlobal('ResizeObserver', StubResizeObserver);

const getGenerationById = vi.fn();
const openToolShortcut = vi.fn(() => true);
vi.mock('$lib/tool-tabs/openShortcut', () => ({
	openToolShortcut: (...args: unknown[]) => (openToolShortcut as (...a: unknown[]) => boolean)(...args)
}));

vi.mock('$lib/media/editors/MediaEditors.svelte', async () => ({
	default: (await import('./fixtures/MediaEditorsStub.svelte')).default
}));
vi.mock('$lib/services/api/mediaTools', () => ({
	listMediaTools: vi.fn().mockResolvedValue([]),
	startMediaToolRun: vi.fn(),
	getMediaToolRun: vi.fn(),
	cancelMediaToolRun: vi.fn(),
	previewMediaTool: vi.fn()
}));
vi.mock('$lib/services/api/index', () => ({
	api: {
		getOrganizeProvenance: vi.fn(() => Promise.resolve({ success: true, data: [] })),
		listLibraryItems: vi.fn().mockResolvedValue({ success: false }),
		getLibraryFacets: vi.fn().mockResolvedValue({ success: false }),
		listCollections: vi.fn().mockResolvedValue({ success: false }),
		getTags: vi.fn().mockResolvedValue({ success: false }),
		getGenerationById: (...args: unknown[]) => getGenerationById(...args)
	}
}));

const { default: LibraryItemModal } = await import('../../src/lib/components/library/LibraryItemModal.svelte');
const { createClassComponent } = await import('svelte/legacy');

const row = {
	id: 'row-1',
	filename: 'a.png',
	original_filename: 'a.png',
	media_type: 'image',
	url: '/a.png',
	width: 64,
	height: 64,
	tags: []
};

const instances: Array<{ $destroy: () => void }> = [];

function mount(item: Record<string, unknown>, props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	instances.push(
		createClassComponent({
			component: LibraryItemModal as never,
			target,
			props: { item, onClose: vi.fn(), onDeleteRequest: vi.fn(), ...props }
		})
	);
}

const line = () => document.body.querySelector('[data-made-in-editor]');
const action = () => document.body.querySelector<HTMLButtonElement>('[data-continue-edit]');

beforeEach(() => {
	openToolShortcut.mockClear();
	getGenerationById.mockReset().mockResolvedValue({ success: true, data: { form_data: { prompt: 'a lighthouse\nat dusk' } } });
});

afterEach(() => {
	for (const instance of instances.splice(0)) instance.$destroy();
	document.body.innerHTML = '';
});

const link = { edit_id: 'ed_1', tool: 'image', revision: 3, available: true, continuable: true };

describe('Made in the editor', () => {
	it('says so and offers Continue this edit while the edit can be continued', async () => {
		const onClose = vi.fn();
		mount({ ...row, made_in_editor: link }, { onClose });
		await settle();
		expect(line()?.textContent).toContain('Made in the editor');
		expect(action()?.textContent?.trim()).toBe('Continue this edit');
		action()?.click();
		expect(openToolShortcut).toHaveBeenCalledTimes(1);
		const [id, items] = openToolShortcut.mock.calls[0] as unknown as [string, Array<{ madeInEditor: { editId: string } }>];
		expect(id).toBe('continue-edit');
		expect(items[0].madeInEditor.editId).toBe('ed_1');
		expect(onClose).toHaveBeenCalled();
	});

	it('shows the line without an action once the edit is gone', async () => {
		mount({ ...row, made_in_editor: { ...link, available: false } });
		await settle();
		expect(line()?.textContent).toContain('Made in the editor');
		expect(action()).toBeNull();
	});

	it('shows the line without an action for a picture saved over the original', async () => {
		mount({ ...row, made_in_editor: { ...link, continuable: false } });
		await settle();
		expect(line()?.textContent).toContain('Made in the editor');
		expect(action()).toBeNull();
	});

	it('uses plain words with no ids and no revision', async () => {
		mount({ ...row, made_in_editor: link });
		await settle();
		const text = line()?.textContent ?? '';
		expect(text).not.toMatch(/ed_1|revision|session/i);
	});

	it('shows nothing for a picture that was not made in the editor, or from an older server', async () => {
		mount(row);
		await settle();
		expect(line()).toBeNull();
		expect(action()).toBeNull();
	});

	it('sits beside the sheet origin of a slice', async () => {
		mount({ ...row, made_in_editor: link, provenance: { slice: { index: 0, count: 4, source_name: 'sheet.png' } } });
		await settle();
		expect(document.body.querySelector('[data-slice-origin]')).not.toBeNull();
		expect(line()).not.toBeNull();
	});
});
