import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { get } from 'svelte/store';
import { mountComponent } from '../helpers/mount';
import { settleFlush } from '../helpers/settle';
import { tabsStore } from '$lib/stores/tabs';
import { activeConfirm, settleConfirm } from '$lib/stores/confirm';
import { toasts } from '$lib/stores/toast';
import { isToolTab, type ToolTab } from '$lib/types/tabs';
import { emptyImageState, writeImageState } from '$lib/tool-tabs/image/state';

const mocks = vi.hoisted(() => ({
	listEdits: vi.fn(),
	getEdit: vi.fn(),
	renameEdit: vi.fn(),
	duplicateEdit: vi.fn(),
	deleteEdit: vi.fn(),
	getLibraryItem: vi.fn(),
	getGenerationHistory: vi.fn()
}));

vi.mock('$lib/services/api/index', () => ({ api: mocks }));

const { default: ImageStartScreen } = await import('$lib/components/tool-tabs/image/ImageStartScreen.svelte');

function edit(n: number, extra: Record<string, unknown> = {}) {
	return {
		id: `e${n}`,
		tool: 'image',
		name: null,
		source: { kind: n % 2 ? 'library' : 'history', id: `s${n}`, name: `picture-${n}.png` },
		thumbnail_upload_id: null,
		revision: 2,
		created_at: '2026-11-16T10:00:00Z',
		updated_at: '2026-11-16T10:00:00Z',
		...extra
	};
}

function list(total: number, shown = Math.min(total, 12), extra: Record<string, unknown> = {}) {
	return { success: true, data: { edits: Array.from({ length: shown }, (_, index) => edit(index + 1, index === 0 ? extra : {})), total, limit: 12, offset: 0 } };
}

const NONE = { success: true, data: { edits: [], total: 0, limit: 12, offset: 0 } };

function history() {
	const now = new Date().toISOString();
	return {
		success: true,
		data: {
			generations: [
				{
					id: 'g1',
					created_at: now,
					files: [
						{ id: 11, file_path: 'outputs/d/g1/harbor-fog.png', file_type: 'image', is_final: true, created_at: now },
						{ id: 12, file_path: 'outputs/d/g1/glass-study.png', file_type: 'image', is_final: true, created_at: now }
					]
				}
			]
		}
	};
}

function render(props: Record<string, unknown> = {}) {
	const onpick = vi.fn();
	const onfile = vi.fn();
	const view = mountComponent(ImageStartScreen, { tabId: 'tab-self', onpick, onfile, ...props });
	const q = <T extends Element = HTMLElement>(selector: string) => view.target.querySelector<T>(selector);
	const all = (selector: string) => Array.from(view.target.querySelectorAll<HTMLElement>(selector));
	const button = (text: string) => all('button').find((node) => node.textContent?.trim() === text)!;
	return { view, q, all, button, onpick, onfile };
}

describe('ImageStartScreen', () => {
	beforeEach(() => {
		tabsStore.reset();
		mocks.listEdits.mockReset().mockResolvedValue(list(27));
		mocks.getEdit.mockReset();
		mocks.renameEdit.mockReset();
		mocks.duplicateEdit.mockReset();
		mocks.deleteEdit.mockReset();
		mocks.getLibraryItem
			.mockReset()
			.mockResolvedValue({ success: true, data: { id: 's1', filename: 's1.png', original_filename: 'picture-1.png', media_type: 'image', url: '/api/media/uploads/s1.png' } });
		mocks.getGenerationHistory.mockReset().mockResolvedValue(history());
	});

	afterEach(() => {
		vi.useRealTimers();
		tabsStore.reset();
	});

	it('lists twelve edits with the count, the search and the numbered pages', async () => {
		const { q, all } = render();
		expect(q('[data-edits-loading]')).not.toBeNull();
		await settleFlush();
		expect(q('[data-testid="tool-tab-empty"]')?.textContent).toContain('Open a picture to edit');
		expect(all('[data-edit-tile]')).toHaveLength(12);
		expect(q('h1')?.textContent).toBe('Recent edits');
		expect(q('[data-recent-edits]')?.textContent).toContain('27');
		expect(q('[data-recent-edits]')?.textContent).toContain('edits');
		expect(q('input[aria-label="Search edits by name"]')).not.toBeNull();
		expect(q('nav[aria-label="Pagination"]')).not.toBeNull();
		expect(mocks.listEdits).toHaveBeenCalledTimes(1);
		expect(mocks.listEdits).toHaveBeenCalledWith({ tool: 'image', limit: 12, offset: 0 });
	});

	it('names an unnamed edit after its picture and marks the one open in another tab', async () => {
		const otherId = tabsStore.openToolTab('image', { path: '/m/x.png', url: '/m/x.png', name: 'x.png', item_id: 'z' });
		tabsStore.updateToolTab(otherId, { state: { v: 1, edit: { id: 'e2', revision: 2 } } });
		tabsStore.openToolTab('image', null);
		const { all, q } = render();
		await settleFlush();
		const names = all('[data-edit-name]').map((node) => node.textContent?.trim());
		expect(names.slice(0, 2)).toEqual(['picture-1', 'picture-2']);
		expect(all('[data-open-elsewhere]')).toHaveLength(1);
		expect(q('[data-edit-tile="e2"] [data-open-badge]')?.textContent).toContain('Open in another tab');
		expect(q('[data-edit-tile="e2"] [data-edit-open]')?.getAttribute('aria-label')).toBe('Go to the open tab: picture-2');
	});

	it('shows a named edit as written', async () => {
		mocks.listEdits.mockResolvedValue(list(3, 3, { name: 'Cover crop, warm' }));
		const { all } = render();
		await settleFlush();
		expect(all('[data-edit-name]')[0].textContent?.trim()).toBe('Cover crop, warm');
	});

	it('first use: empty state, no search, no count, recent History below and the start panel', async () => {
		mocks.listEdits.mockResolvedValue(NONE);
		const { q, all, onpick } = render();
		await settleFlush();
		expect(q('[data-recent-edits]')?.dataset.state).toBe('first-use');
		expect(q('[data-empty-state="page"]')?.textContent).toContain('No edits yet');
		expect(q('input[aria-label="Search edits by name"]')).toBeNull();
		expect(q('[data-recent-history]')).not.toBeNull();
		expect(all('[data-recent-history] [data-edit-tile]')).toHaveLength(2);
		expect(all('[data-recent-history] button')).toHaveLength(2);
		expect(all('[data-start-source]').map((node) => node.dataset.startSource)).toEqual(['browse', 'history', 'library', 'kits']);
		expect(all('[data-start-source]').map((node) => node.getAttribute('aria-label'))).toEqual(['Upload file', 'History', 'Library', 'Kits']);
		q<HTMLButtonElement>('[data-recent-history] button')?.click();
		expect(onpick).toHaveBeenCalledTimes(1);
		expect(onpick.mock.calls[0][0]).toMatchObject({ generation_file_id: '11' });
	});

	it('first use with no History shows the empty state alone', async () => {
		mocks.listEdits.mockResolvedValue(NONE);
		mocks.getGenerationHistory.mockResolvedValue({ success: true, data: { generations: [] } });
		const { q } = render();
		await settleFlush();
		expect(q('[data-empty-state="page"]')).not.toBeNull();
		expect(q('[data-recent-history]')).toBeNull();
	});

	it('a search with no match shows the compact empty state and a way to clear it', async () => {
		const { q, all, button } = render();
		await settleFlush();
		mocks.listEdits.mockResolvedValue(NONE);
		vi.useFakeTimers();
		const input = q<HTMLInputElement>('input[aria-label="Search edits by name"]')!;
		input.value = 'lantern';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await vi.advanceTimersByTimeAsync(400);
		vi.useRealTimers();
		await settleFlush();
		expect(q('[data-empty-state="compact"]')?.textContent).toContain('No matching edits');
		expect(q('[data-empty-state="compact"]')?.textContent).toContain('lantern');
		expect(q('[data-recent-edits]')?.textContent).toContain('0 of 27');
		mocks.listEdits.mockResolvedValue(list(27));
		button('Clear search').click();
		await settleFlush();
		expect(q('[data-empty-state]')).toBeNull();
		expect(all('[data-edit-tile]')).toHaveLength(12);
	});

	it('a failed load shows the error state and Retry loads again', async () => {
		mocks.listEdits.mockRejectedValueOnce({ response: { status: 500, data: {} } });
		const { q, all, button } = render();
		await settleFlush();
		expect(q('[data-empty-state="compact"]')?.textContent).toContain("Couldn't load your edits");
		expect(q<HTMLInputElement>('input[aria-label="Search edits by name"]')?.disabled).toBe(true);
		button('Retry').click();
		await settleFlush();
		expect(all('[data-edit-tile]')).toHaveLength(12);
	});

	it('clicking a picture continues the edit in this tab and links it', async () => {
		const tab = tabsStore.openToolTab('image', null);
		mocks.getEdit.mockResolvedValue({ success: true, data: { edit: { ...edit(1), revision: 5, state: writeImageState(emptyImageState('crop')) } } });
		const { q } = render({ tabId: tab });
		await settleFlush();
		q<HTMLButtonElement>('[data-edit-tile="e1"] [data-edit-open]')!.click();
		await settleFlush();
		const restored = get(tabsStore).tabs.find((candidate) => candidate.id === tab) as ToolTab;
		expect(isToolTab(restored)).toBe(true);
		expect(restored.source).toMatchObject({ item_id: 's1', url: '/api/media/uploads/s1.png' });
		expect(restored.state).toMatchObject({ edit: { id: 'e1', revision: 5 } });
		expect(restored.epoch).toBe(1);
	});

	it('clicking an edit that is open elsewhere switches to that tab', async () => {
		const other = tabsStore.openToolTab('image', { path: '/m/x.png', url: '/m/x.png', name: 'x.png', item_id: 'z' });
		tabsStore.updateToolTab(other, { state: { v: 1, edit: { id: 'e2', revision: 2 } } });
		const mine = tabsStore.openToolTab('image', null);
		expect(mine).not.toBe(other);
		const { q } = render({ tabId: mine });
		await settleFlush();
		q<HTMLButtonElement>('[data-edit-tile="e2"] [data-edit-open]')!.click();
		await settleFlush();
		expect(get(tabsStore).activeTabId).toBe(other);
		expect(mocks.getEdit).not.toHaveBeenCalled();
	});

	it('a deleted edit shows a message and refreshes the list', async () => {
		mocks.getEdit.mockRejectedValue({ response: { status: 404, data: {} } });
		const { q, all } = render();
		await settleFlush();
		mocks.listEdits.mockResolvedValue(list(26, 11));
		q<HTMLButtonElement>('[data-edit-tile="e3"] [data-edit-open]')!.click();
		await settleFlush();
		expect(mocks.listEdits).toHaveBeenCalledTimes(2);
		expect(all('[data-edit-tile]')).toHaveLength(11);
		expect(get(toasts).map((toast) => toast.message)).toContain('That edit no longer exists.');
	});

	it('Delete asks in the standard confirm and removes the edit', async () => {
		const { q } = render();
		await settleFlush();
		mocks.deleteEdit.mockResolvedValue(undefined);
		q<HTMLButtonElement>('[data-edit-tile="e1"] button[aria-label^="Options for"]')!.click();
		flushSync();
		const item = Array.from(document.body.querySelectorAll<HTMLElement>('[role="menuitem"]')).find((node) => node.textContent?.trim() === 'Delete')!;
		item.click();
		await settleFlush();
		const request = get(activeConfirm);
		expect(request?.title).toBe('Delete this edit?');
		expect(request?.message).toContain('The original picture is not affected.');
		expect(request?.confirmLabel).toBe('Delete edit');
		expect(mocks.deleteEdit).not.toHaveBeenCalled();
		settleConfirm(request!.id, true);
		await settleFlush();
		expect(mocks.deleteEdit).toHaveBeenCalledWith('e1');
		expect(mocks.listEdits).toHaveBeenCalledTimes(2);
	});
});
