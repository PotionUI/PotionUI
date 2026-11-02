import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { settle } from './helpers/settle';
import { CORE_TOOLS } from './fixtures/mediaToolCatalog';

const startMediaToolRun = vi.fn();
const getMediaToolRun = vi.fn();
const listGenerationMedia = vi.fn();

vi.mock('$lib/services/api/mediaTools', () => ({
	listMediaTools: vi.fn().mockResolvedValue(CORE_TOOLS),
	startMediaToolRun: (...args: unknown[]) => startMediaToolRun(...args),
	getMediaToolRun: (...args: unknown[]) => getMediaToolRun(...args),
	cancelMediaToolRun: vi.fn(),
	previewMediaTool: vi.fn()
}));

vi.mock('$lib/services/api/index', () => ({
	api: {
		listGenerationMedia: (...args: unknown[]) => listGenerationMedia(...args),
		listUploads: vi.fn().mockResolvedValue({ success: true, data: { uploads: [], total: 0, limit: 100, offset: 0 } })
	}
}));

const { openMediaTool } = await import('$lib/media-tools/open');
const { SAVED_TO_LIBRARY_ANNOUNCE } = await import('$lib/media-tools/announce');
const { mediaToolRuns } = await import('$lib/media-tools/runs');
const { resetMediaToolCatalog } = await import('$lib/media-tools/catalog');
const { toasts } = await import('$lib/stores/toast');
const { page } = await import('./stubs/appStores');
const { pickTool } = await import('$lib/tools/toolPicker');
const { mediaToolFromCatalog } = await import('$lib/tools/catalogTools');
const { unregisterMediaTools } = await import('$lib/tools/tools');

const RESULT = { id: 'new-1', filename: 'rotated.png', original_filename: 'sunset-rotated.png', media_type: 'image', url: '/rotated.png' };

const generation = {
	id: 'gen-1:0',
	kind: 'image' as const,
	url: '/api/media/generations/gen-1/0.png',
	filename: '0.png',
	width: 512,
	height: 512,
	generationId: 'gen-1',
	paramIndex: 0
};

function historyContext() {
	return {
		scope: 'history' as const,
		items: [{ ...generation, kind: 'audio' as const }],
		kinds: new Set(['audio' as const]),
		collectionId: null,
		generations: [],
		generationIds: ['gen-1'],
		files: []
	};
}

function successToasts() {
	return get(toasts).filter((entry) => entry.type === 'success');
}

async function applyDialog() {
	await settle();
	const apply = Array.from(document.body.querySelectorAll('button')).find((button) => (button.textContent || '').trim() === 'Apply');
	apply!.click();
	await settle();
}

beforeEach(() => {
	document.body.innerHTML = '';
	resetMediaToolCatalog();
	unregisterMediaTools((tool) => !!tool.catalogToolId);
	for (const entry of get(toasts)) toasts.remove(entry.id);
	startMediaToolRun.mockReset().mockResolvedValue({ run_id: 'run-1' });
	getMediaToolRun.mockReset().mockResolvedValue({
		run_id: 'run-1',
		status: 'completed',
		progress: 1,
		label: '',
		items: [RESULT],
		replaced: false,
		error: null
	});
	listGenerationMedia.mockReset().mockResolvedValue({ success: true, data: { media: [{ id: 'file-1', filename: '0.png' }] } });
	page.set({ url: new URL('http://localhost/history') } as never);
});

afterEach(() => {
	mediaToolRuns.destroy();
});

describe('a tool run from History', () => {
	it('shows exactly one Saved to Library toast, and it links to the Library', async () => {
		await openMediaTool({ toolId: 'rotate', items: [generation], scope: 'history', announce: SAVED_TO_LIBRARY_ANNOUNCE });
		await applyDialog();

		const saved = successToasts();
		expect(saved).toHaveLength(1);
		expect(saved[0].title).toBe('Saved to Library');
		expect(saved[0].message).toBe('sunset-rotated.png is in your library.');
		expect(saved[0].action?.label).toBe('Open Library');

		saved[0].action!.onClick();
		await settle();
		expect(get(page).url.pathname).toBe('/library');
	});

	it('runs the file from storage as a new item', async () => {
		await openMediaTool({ toolId: 'rotate', items: [generation], scope: 'history', announce: SAVED_TO_LIBRARY_ANNOUNCE });
		await applyDialog();

		expect(startMediaToolRun).toHaveBeenCalledWith({
			steps: [{ tool: 'rotate', params: {} }],
			inputs: [{ generation_file_id: 'file-1' }],
			output: { mode: 'new' }
		});
	});

	it('says nothing on success through the Tools menu path, and hands the result to the page', async () => {
		const onResult = vi.fn();
		pickTool(mediaToolFromCatalog(CORE_TOOLS.find((tool) => tool.id === 'speed')!), historyContext(), {
			mount: vi.fn(),
			onResult
		});
		await applyDialog();

		expect(successToasts()).toHaveLength(0);
		expect(get(toasts).filter((entry) => (entry.title ?? '').includes('Saved'))).toHaveLength(0);
		expect(onResult).toHaveBeenCalledTimes(1);
		expect(onResult.mock.calls[0][0]).toEqual([RESULT]);
	});

	it('lets a host that wants no toast of its own switch it off, and still hears about a failure', async () => {
		await openMediaTool({ toolId: 'rotate', items: [generation], scope: 'history', announce: false });
		await applyDialog();
		expect(get(toasts)).toEqual([]);

		document.body.innerHTML = '';
		getMediaToolRun.mockResolvedValue({
			run_id: 'run-1',
			status: 'failed',
			progress: 0,
			label: '',
			items: [],
			replaced: false,
			error: { code: 'x', message: 'The encoder stopped' }
		});
		await openMediaTool({ toolId: 'rotate', items: [generation], scope: 'history', announce: false });
		await applyDialog();
		expect(get(toasts).map((entry) => entry.message)).toEqual(['The encoder stopped']);
	});

	it('gives a Library run the plain toast with no link', async () => {
		await openMediaTool({ toolId: 'rotate', items: [{ ...generation, id: 'row-5', generationId: undefined, itemId: 'row-5' }], scope: 'library' });
		await applyDialog();

		const saved = successToasts();
		expect(saved).toHaveLength(1);
		expect(saved[0].title).toBe('Saved as new');
		expect(saved[0].action).toBeUndefined();
	});

	it('does not announce a field edit, which writes into the field instead', async () => {
		await openMediaTool({ toolId: 'rotate', items: [{ ...generation, id: 'field', generationId: undefined, itemId: 'row-5' }], scope: 'field', announce: SAVED_TO_LIBRARY_ANNOUNCE });
		await applyDialog();
		expect(get(toasts)).toEqual([]);
	});
});
