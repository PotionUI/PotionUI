// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { settle } from './helpers/settle';

const toastError = vi.hoisted(() => vi.fn());
vi.mock('$lib/stores/toast', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/stores/toast')>();
	return { ...actual, toasts: { ...actual.toasts, error: toastError } };
});
vi.mock('$lib/components/library/LibraryItemModal.svelte', () => {
	throw new Error('chunk failed to load');
});
vi.mock('$app/navigation', () => ({ pushState: vi.fn(), goto: vi.fn() }));
vi.mock('$lib/services/api/index', () => ({
	api: {
		listLibraryItems: vi.fn().mockResolvedValue({ success: false }),
		getLibraryFacets: vi.fn().mockResolvedValue({ success: false }),
		listCollections: vi.fn().mockResolvedValue({ success: false }),
		getTags: vi.fn().mockResolvedValue({ success: false })
	}
}));

const { default: LibraryModalHost } = await import('../../src/lib/components/library/LibraryModalHost.svelte');
const { openLibraryResultModal, closeLibraryResultModal, libraryModalRequest } = await import('$lib/library/landing');
const { setPageState } = await import('./stubs/appState.svelte');
const { mount, unmount, flushSync } = await import('svelte');
const { get } = await import('svelte/store');

const item = { id: 'lib-1', filename: 'a.png', original_filename: 'a.png', media_type: 'image', url: '/a.png', tags: [] } as never;

let instance: Record<string, unknown> | undefined;
let target: HTMLElement | undefined;

beforeEach(() => {
	toastError.mockClear();
	setPageState(new URL('http://localhost/history'), {});
	closeLibraryResultModal();
});

afterEach(() => {
	if (instance) unmount(instance);
	target?.remove();
	instance = undefined;
	document.body.innerHTML = '';
});

describe('the Library modal when its chunk fails to load', () => {
	it('toasts the failure, clears the request and retries on the next open', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(LibraryModalHost, { target });
		flushSync();

		openLibraryResultModal(item, null);
		await vi.waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
		await settle(3);
		expect(document.body.querySelector('[role="dialog"]')).toBeNull();
		expect(get(libraryModalRequest)).toBeNull();

		openLibraryResultModal(item, null);
		await vi.waitFor(() => expect(toastError).toHaveBeenCalledTimes(2));
	});
});
