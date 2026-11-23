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

const pushState = vi.fn((_url: string, state: Record<string, unknown>) => {
	pushedState = state;
});
let pushedState: Record<string, unknown> | null = null;

vi.mock('$app/navigation', () => ({
	pushState: (url: string, state: Record<string, unknown>) => pushState(url, state),
	goto: vi.fn()
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
		getGenerationById: vi.fn().mockResolvedValue({ success: true, data: { form_data: { prompt: 'p' } } })
	}
}));

const { default: LibraryModalHost } = await import('../../src/lib/components/library/LibraryModalHost.svelte');
const { openLibraryResultModal, closeLibraryResultModal, libraryModalRequest } = await import('$lib/library/landing');
const { registerDetailsReturn } = await import('$lib/tools/detailsReturn');
const { libraryStore } = await import('$lib/stores/library');
const { setPageState } = await import('./stubs/appState.svelte');
const { mount, unmount, flushSync } = await import('svelte');
const { get } = await import('svelte/store');

const item = {
	id: 'lib-1',
	filename: 'a.png',
	original_filename: 'a.png',
	media_type: 'image',
	url: '/a.png',
	width: 64,
	height: 64,
	tags: []
} as never;
const source = { generationId: 'g1', fileIndex: 2 };

let instance: Record<string, unknown> | undefined;
let target: HTMLElement | undefined;
const returned = vi.fn();
let unregister: (() => void) | undefined;
const historyBack = vi.fn();

function setPage(pathname: string, state: Record<string, unknown> = {}) {
	setPageState(new URL(`http://localhost${pathname}`), state);
}

function mountHost() {
	pushState.mockImplementation((_url: string, state: Record<string, unknown>) => {
		pushedState = state;
		setPage('/history', state);
	});
	target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(LibraryModalHost, { target });
	flushSync();
}

const modal = () => document.body.querySelector('[role="dialog"]');

beforeEach(() => {
	pushState.mockClear();
	pushedState = null;
	returned.mockClear();
	historyBack.mockClear();
	vi.spyOn(window.history, 'back').mockImplementation(historyBack);
	unregister = registerDetailsReturn(returned);
	setPage('/history');
	closeLibraryResultModal();
	libraryStore.reset();
});

afterEach(() => {
	if (instance) unmount(instance);
	target?.remove();
	instance = undefined;
	unregister?.();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('the Library modal over History', () => {
	it('stays open while the shallow entry is still being pushed and the page state has not caught up', async () => {
		pushState.mockImplementation((_url: string, state: Record<string, unknown>) => {
			pushedState = state;
		});
		target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(LibraryModalHost, { target });
		flushSync();

		openLibraryResultModal(item, source);
		await vi.waitFor(() => expect(modal()).not.toBeNull(), { timeout: 20000 });

		expect(modal()).not.toBeNull();
		expect(returned).not.toHaveBeenCalled();
	});

	it('opens for a landing request and pushes a shallow history entry, without leaving the page', async () => {
		mountHost();
		openLibraryResultModal(item, source);
		await vi.waitFor(() => expect(modal()).not.toBeNull(), { timeout: 20000 });

		expect(modal()).not.toBeNull();
		expect(document.body.textContent).toContain('a.png');
		expect(pushState).toHaveBeenCalledWith('', { libraryModal: 'lib-1' });
	});

	it('shows Back to the generation for the source generation', async () => {
		mountHost();
		openLibraryResultModal(item, source);
		await vi.waitFor(() => expect(modal()).not.toBeNull(), { timeout: 20000 });

		expect(document.body.querySelector('[data-back-to-generation] button')?.textContent).toContain('Back to the generation');
	});

	it('closes and reopens the History details on the same generation and file when browser Back pops the entry', async () => {
		mountHost();
		openLibraryResultModal(item, source);
		await vi.waitFor(() => expect(modal()).not.toBeNull(), { timeout: 20000 });

		setPage('/history', {});
		await settle(4);

		expect(modal()).toBeNull();
		expect(get(libraryModalRequest)).toBeNull();
		expect(returned).toHaveBeenCalledWith(source);
	});

	it('goes back in the browser history on close, so Escape and the close button behave like Back', async () => {
		mountHost();
		openLibraryResultModal(item, source);
		await vi.waitFor(() => expect(modal()).not.toBeNull(), { timeout: 20000 });
		setPage('/history', { libraryModal: 'lib-1' });
		await settle(2);

		document.body.querySelector<HTMLElement>('[aria-label="Close"], [aria-label="Close modal"]')?.click();
		(document.body.querySelector('[data-back-to-generation] button') as HTMLElement).click();
		await settle(2);

		expect(historyBack).toHaveBeenCalled();
		expect(returned).not.toHaveBeenCalled();
	});

	it('stays out of the way on the Library page, which shows its own modal', async () => {
		setPage('/library');
		mountHost();
		openLibraryResultModal(item, source);
		await settle(4);

		expect(modal()).toBeNull();
		expect(pushState).not.toHaveBeenCalled();
	});
});
