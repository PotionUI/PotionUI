import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { get } from 'svelte/store';
import { CORE_TOOLS } from './fixtures/mediaToolCatalog';
import { settle } from './helpers/settle';

const openMediaTool = vi.fn();
const goto = vi.fn().mockResolvedValue(undefined);
const returned = vi.fn();
const listMediaTools = vi.fn();

vi.mock('$lib/media-tools/open', () => ({
	openMediaTool: (...args: unknown[]) => openMediaTool(...args)
}));
vi.mock('$app/navigation', () => ({ goto: (...args: unknown[]) => goto(...args) }));
vi.mock('$lib/services/api/mediaTools', () => ({ listMediaTools: (...args: unknown[]) => listMediaTools(...args) }));
vi.mock('$lib/services/api/index', () => ({
	api: {
		listLibraryItems: vi.fn().mockResolvedValue({ success: false }),
		getLibraryFacets: vi.fn().mockResolvedValue({ success: false }),
		listCollections: vi.fn().mockResolvedValue({ success: false })
	}
}));
vi.mock('$lib/components/plugins/PluginSlot.svelte', async () => ({
	default: (await import('./fixtures/PluginSlotStub.svelte')).default
}));

const { default: DetailsActionBar } = await import('$lib/media-tools/DetailsActionBar.svelte');
const { takePendingFresh } = await import('$lib/library/freshHandoff');
const { libraryModalRequest, closeLibraryResultModal } = await import('$lib/library/landing');
const { registerDetailsReturn } = await import('$lib/tools/detailsReturn');
const { registerDismissLayer, unregisterDismissLayer } = await import('$lib/actions/layerStack');
const { resetMediaToolCatalog } = await import('$lib/media-tools/catalog');
const { unregisterMediaTools } = await import('$lib/tools/tools');
const { registerEntryTools } = await import('$lib/tools/entryTools');
const { toasts } = await import('$lib/stores/toast');

registerEntryTools();

const video = {
	id: 'row-1',
	kind: 'audio' as const,
	url: '/api/media/uploads/clip.wav',
	filename: 'clip.wav',
	itemId: 'row-1'
};

let cleanup: (() => void) | undefined;

async function render(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = mount(DetailsActionBar, {
		target,
		props: { items: [video], scope: 'library', ...props }
	});
	cleanup = () => {
		unmount(instance);
		target.remove();
	};
	await settle();
	flushSync();
	return target;
}

function pick(_root: ParentNode, text: string): HTMLButtonElement {
	const trigger = document.body.querySelector<HTMLButtonElement>('[data-bar-item="edit"]')!;
	trigger.click();
	flushSync();
	return Array.from(document.body.querySelectorAll<HTMLButtonElement>('[data-bar-menu] [role="menuitem"]')).find(
		(entry) => (entry.querySelector('span')?.textContent ?? '').trim() === text
	)!;
}

beforeEach(() => {
	openMediaTool.mockReset();
	goto.mockClear();
	takePendingFresh();
	closeLibraryResultModal();
	returned.mockClear();
	listMediaTools.mockReset().mockResolvedValue(CORE_TOOLS);
	resetMediaToolCatalog();
	unregisterMediaTools((tool) => !!tool.catalogToolId);
	for (const entry of get(toasts)) toasts.remove(entry.id);
});

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
});

describe('the bar on History', () => {
	const source = { generationId: 'g1', fileIndex: 0 };

	it('closes the details first, then opens the tool, with quiet success and a retry', async () => {
		const onLaunch = vi.fn();
		const root = await render({ scope: 'history', onLaunch });

		pick(root, 'Trim').click();

		expect(onLaunch).toHaveBeenCalledTimes(1);
		expect(openMediaTool).toHaveBeenCalledTimes(1);
		expect(onLaunch.mock.invocationCallOrder[0]).toBeLessThan(openMediaTool.mock.invocationCallOrder[0]);
		const call = openMediaTool.mock.calls[0][0];
		expect(call).toMatchObject({ toolId: 'trim', scope: 'history' });
		expect(call.announce.quietSuccess).toBe(true);
		expect(typeof call.announce.retry).toBe('function');
	});

	it('opens the Library modal over History for exactly one result, with no navigation and no toast', async () => {
		const root = await render({ scope: 'history', items: [{ ...video, generationId: 'g1', paramIndex: 0 }] });
		pick(root, 'Trim').click();
		await settle(3);

		await openMediaTool.mock.calls[0][0].onResult(
			[{ id: 'lib-9', filename: 'a.mp4', media_type: 'video', url: '/a' }],
			{ toolId: 'trim', replaced: false }
		);

		expect(get(libraryModalRequest)?.item.id).toBe('lib-9');
		expect(get(libraryModalRequest)?.source).toEqual(source);
		expect(goto).not.toHaveBeenCalled();
		expect(get(toasts).filter((entry) => entry.type === 'success')).toHaveLength(0);
	});

	it('toasts and tags the results new, without navigating, for several results', async () => {
		const root = await render({ scope: 'history' });
		pick(root, 'Trim').click();

		await openMediaTool.mock.calls[0][0].onResult(
			[
				{ id: 'a', filename: 'a.mp4', media_type: 'video', url: '/a' },
				{ id: 'b', filename: 'b.mp4', media_type: 'video', url: '/b' }
			],
			{ toolId: 'split', replaced: false }
		);

		expect(goto).not.toHaveBeenCalled();
		expect(get(libraryModalRequest)).toBeNull();
		expect(get(toasts).some((entry) => entry.message === '2 clips saved to Library, tagged New')).toBe(true);
		expect(takePendingFresh()).toEqual(['a', 'b']);
	});

	it('reopens the same generation and file when the tool closes without a result', async () => {
		const unregister = registerDetailsReturn(returned);
		const root = await render({ scope: 'history', items: [{ ...video, generationId: 'g7', paramIndex: 2 }] });
		pick(root, 'Trim').click();

		openMediaTool.mock.calls[0][0].onClose();

		expect(returned).toHaveBeenCalledWith({ generationId: 'g7', fileIndex: 2 });
		unregister();
	});

	it('does not reopen anything once a result was delivered', async () => {
		const unregister = registerDetailsReturn(returned);
		const root = await render({ scope: 'history' });
		pick(root, 'Trim').click();

		await openMediaTool.mock.calls[0][0].onResult([], { toolId: 'trim', replaced: false });
		openMediaTool.mock.calls[0][0].onClose();

		expect(returned).not.toHaveBeenCalled();
		unregister();
	});

	it('relaunches the same tool on the same items from the Try again action', async () => {
		const root = await render({ scope: 'history' });
		pick(root, 'Trim').click();

		openMediaTool.mock.calls[0][0].announce.retry();

		expect(openMediaTool).toHaveBeenCalledTimes(2);
		expect(openMediaTool.mock.calls[1][0]).toMatchObject({ toolId: 'trim', scope: 'history' });
	});

	it('opens the Library modal only when nothing else is in the way, and otherwise toasts with Open', async () => {
		const root = await render({ scope: 'history' });
		pick(root, 'Trim').click();
		const layer = registerDismissLayer({ node: document.createElement('div') });

		await openMediaTool.mock.calls[0][0].onResult(
			[{ id: 'lib-2', filename: 'a.mp4', media_type: 'video', url: '/a' }],
			{ toolId: 'trim', replaced: false }
		);
		unregisterDismissLayer(layer);

		expect(get(libraryModalRequest)).toBeNull();
		const saved = get(toasts).find((entry) => entry.message === 'Saved to Library');
		expect(saved?.action?.label).toBe('Open');
	});
});

describe('the bar on the Library', () => {
	it('opens a tool on the Library with the default toast', async () => {
		const root = await render({ scope: 'library' });

		pick(root, 'Trim').click();

		expect(openMediaTool.mock.calls[0][0].scope).toBe('library');
		expect(openMediaTool.mock.calls[0][0].announce).toBeUndefined();
	});
});
