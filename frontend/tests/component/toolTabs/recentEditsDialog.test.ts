import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { get } from 'svelte/store';
import { mountComponent } from '../helpers/mount';
import { stubClientWidth } from '../helpers/stubClientWidth';
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
	getLibraryItem: vi.fn()
}));

vi.mock('$lib/services/api/index', () => ({ api: mocks }));

const { default: RecentEditsDialog } = await import('$lib/components/tool-tabs/image/RecentEditsDialog.svelte');

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

function render(props: Record<string, unknown> = {}) {
	const onclose = vi.fn();
	const view = mountComponent(RecentEditsDialog, { tabId: 'tab-self', onclose, ...props });
	const q = <T extends Element = HTMLElement>(selector: string) => document.body.querySelector<T>(selector);
	const all = (selector: string) => Array.from(document.body.querySelectorAll<HTMLElement>(selector));
	const button = (text: string) => all('button').find((node) => node.textContent?.trim() === text)!;
	return { view, q, all, button, onclose };
}

describe('RecentEditsDialog', () => {
	let restoreWidth = () => {};

	beforeEach(() => {
		tabsStore.reset();
		restoreWidth = stubClientWidth(1000);
		mocks.listEdits.mockReset().mockResolvedValue(list(27));
		mocks.getEdit.mockReset();
		mocks.renameEdit.mockReset();
		mocks.duplicateEdit.mockReset();
		mocks.deleteEdit.mockReset();
		mocks.getLibraryItem
			.mockReset()
			.mockResolvedValue({ success: true, data: { id: 's1', filename: 's1.png', original_filename: 'picture-1.png', media_type: 'image', url: '/api/media/uploads/s1.png' } });
	});

	afterEach(() => {
		restoreWidth();
		vi.useRealTimers();
		tabsStore.reset();
	});

	it('lists twelve edits with the count, the search and the numbered pages', async () => {
		const { q, all } = render();
		expect(q('[data-edits-loading]')).not.toBeNull();
		await settleFlush();
		expect(q('[role="dialog"] h2')?.textContent).toBe('Recent edits');
		expect(q('[role="dialog"]')?.textContent).toContain('27 edits');
		expect(all('[data-edit-tile]')).toHaveLength(12);
		expect(q('input[aria-label="Search edits by name"]')).not.toBeNull();
		const pages = all('nav[aria-label="Pagination"] button').map((node) => node.textContent?.trim()).filter((text) => /^\d+$/.test(text ?? ''));
		expect(pages).toEqual(['1', '2', '3']);
		expect(mocks.listEdits).toHaveBeenCalledTimes(1);
		expect(mocks.listEdits).toHaveBeenCalledWith({ tool: 'image', limit: 12, offset: 0 });
	});

	it('2 edits: both tiles and no pagination', async () => {
		mocks.listEdits.mockResolvedValue(list(2));
		const { q, all } = render();
		await settleFlush();
		expect(all('[data-edits-list] [data-edit-tile]')).toHaveLength(2);
		expect(q('[role="dialog"]')?.textContent).toContain('2 edits');
		expect(q('nav[aria-label="Pagination"]')).toBeNull();
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
		expect(q('[data-edit-tile="e2"]')?.textContent).toContain('Go to open tab');
		expect(q('[data-edit-tile="e1"]')?.textContent).toContain('Continue this edit');
		expect(all('[data-edit-tile] button[aria-label^="Options for"]')).toHaveLength(12);
	});

	it('shows a named edit as written', async () => {
		mocks.listEdits.mockResolvedValue(list(3, 3, { name: 'Cover crop, warm' }));
		const { all } = render();
		await settleFlush();
		expect(all('[data-edit-name]')[0].textContent?.trim()).toBe('Cover crop, warm');
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
		expect(mocks.listEdits).toHaveBeenLastCalledWith({ tool: 'image', limit: 12, offset: 0, search: 'lantern' });
		expect(q('[data-empty-state="compact"]')?.textContent).toContain('No matching edits');
		expect(q('[data-empty-state="compact"]')?.textContent).toContain('lantern');
		expect(q('[role="dialog"]')?.textContent).toContain('0 of 27');
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

	it('clicking a picture continues the edit in this tab, links it and closes the dialog', async () => {
		const tab = tabsStore.openToolTab('image', null);
		mocks.getEdit.mockResolvedValue({ success: true, data: { edit: { ...edit(1), revision: 5, state: writeImageState(emptyImageState('crop')) } } });
		const { q, onclose } = render({ tabId: tab });
		await settleFlush();
		q<HTMLButtonElement>('[data-edit-tile="e1"] [data-edit-open]')!.click();
		await settleFlush();
		const restored = get(tabsStore).tabs.find((candidate) => candidate.id === tab) as ToolTab;
		expect(isToolTab(restored)).toBe(true);
		expect(restored.source).toMatchObject({ item_id: 's1', url: '/api/media/uploads/s1.png' });
		expect(restored.state).toMatchObject({ edit: { id: 'e1', revision: 5 } });
		expect(restored.epoch).toBe(1);
		expect(onclose).toHaveBeenCalledTimes(1);
	});

	it('opened from an editor tab, an edit opens in a new tab and leaves this one alone', async () => {
		const mine = tabsStore.openToolTab('image', { path: '/m/y.png', url: '/m/y.png', name: 'y.png', item_id: 'y' });
		mocks.getEdit.mockResolvedValue({ success: true, data: { edit: { ...edit(1), revision: 5, state: writeImageState(emptyImageState('crop')) } } });
		const before = get(tabsStore).tabs.length;
		const { q, onclose } = render({ tabId: mine, inPlace: false });
		await settleFlush();
		q<HTMLButtonElement>('[data-edit-tile="e1"] [data-edit-open]')!.click();
		await settleFlush();
		const tabs = get(tabsStore).tabs;
		expect(tabs.length).toBe(before + 1);
		expect((tabs.find((candidate) => candidate.id === mine) as ToolTab).source).toMatchObject({ item_id: 'y' });
		const opened = tabs.find((candidate) => candidate.id === get(tabsStore).activeTabId) as ToolTab;
		expect(opened.id).not.toBe(mine);
		expect(opened.state).toMatchObject({ edit: { id: 'e1', revision: 5 } });
		expect(get(tabsStore).activeTabId).toBe(opened.id);
		expect(onclose).toHaveBeenCalledTimes(1);
	});

	it('clicking an edit that is open elsewhere switches to that tab', async () => {
		const other = tabsStore.openToolTab('image', { path: '/m/x.png', url: '/m/x.png', name: 'x.png', item_id: 'z' });
		tabsStore.updateToolTab(other, { state: { v: 1, edit: { id: 'e2', revision: 2 } } });
		const mine = tabsStore.openToolTab('image', null);
		expect(mine).not.toBe(other);
		const { q, onclose } = render({ tabId: mine });
		await settleFlush();
		q<HTMLButtonElement>('[data-edit-tile="e2"] [data-edit-open]')!.click();
		await settleFlush();
		expect(get(tabsStore).activeTabId).toBe(other);
		expect(mocks.getEdit).not.toHaveBeenCalled();
		expect(onclose).toHaveBeenCalledTimes(1);
	});

	it('a deleted edit shows a message, refreshes the list and keeps the dialog open', async () => {
		mocks.getEdit.mockRejectedValue({ response: { status: 404, data: {} } });
		const { q, all, onclose } = render();
		await settleFlush();
		mocks.listEdits.mockResolvedValue(list(26, 11));
		q<HTMLButtonElement>('[data-edit-tile="e3"] [data-edit-open]')!.click();
		await settleFlush();
		expect(mocks.listEdits).toHaveBeenCalledTimes(2);
		expect(all('[data-edit-tile]')).toHaveLength(11);
		expect(get(toasts).map((toast) => toast.message)).toContain('That edit no longer exists.');
		expect(onclose).not.toHaveBeenCalled();
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

function box(node: HTMLElement) {
	const wrapper = node.closest<HTMLElement>('[data-tile-box]')!;
	const basis = Number(/(\d+(?:\.\d+)?)px/.exec(wrapper.style.flex)![1]);
	const media = wrapper.querySelector<HTMLElement>('[data-edit-open]')!;
	const height = Number(/(\d+(?:\.\d+)?)px/.exec(media.style.height)![1]);
	return { width: basis, height, grow: wrapper.style.flex.startsWith('1 1'), rowEnd: wrapper.dataset.rowEnd === 'true' };
}

function loaded(img: HTMLImageElement, width: number, height: number) {
	Object.defineProperty(img, 'naturalWidth', { configurable: true, value: width });
	Object.defineProperty(img, 'naturalHeight', { configurable: true, value: height });
	img.dispatchEvent(new Event('load'));
	flushSync();
}

describe('RecentEditsDialog justified tiles', () => {
	let restoreWidth = () => {};

	beforeEach(() => {
		restoreWidth = stubClientWidth(1000);
		tabsStore.reset();
		mocks.listEdits.mockReset().mockResolvedValue(list(10));
		mocks.getEdit.mockReset();
		mocks.getLibraryItem
			.mockReset()
			.mockResolvedValue({ success: true, data: { id: 's1', filename: 's1.png', original_filename: 'picture-1.png', media_type: 'image', url: '/api/media/uploads/s1.png' } });
	});

	afterEach(() => {
		restoreWidth();
		tabsStore.reset();
	});

	it('fills the container width on every row but the last, which keeps its own height and is not stretched', async () => {
		const { all } = render();
		await settleFlush();
		const boxes = all('[data-edit-tile]').map(box);
		expect(boxes).toHaveLength(10);
		const rows: (typeof boxes)[] = [];
		let current: typeof boxes = [];
		for (const b of boxes) {
			current.push(b);
			if (b.rowEnd) {
				rows.push(current);
				current = [];
			}
		}
		expect(rows.length).toBeGreaterThan(1);
		const last = rows[rows.length - 1];
		expect(last.some((b) => b.grow)).toBe(false);
		rows.slice(0, -1).forEach((row) => expect(row[row.length - 1].grow).toBe(true));
	});

	it('starts a picture without dimensions at 4:3 and settles on its real shape once it loads', async () => {
		const { all, q } = render();
		await settleFlush();
		const first = q('[data-edit-tile="e1"]')!;
		const before = box(first);
		expect(before.width / before.height).toBeCloseTo(4 / 3, 1);
		loaded(first.querySelector('img')!, 600, 1200);
		await settleFlush();
		const after = box(q('[data-edit-tile="e1"]')!);
		expect(after.width / after.height).toBeCloseTo(0.5, 1);
		expect(all('[data-edit-tile]')).toHaveLength(10);
	});

	it('never crops a picture', async () => {
		const { all } = render();
		await settleFlush();
		expect(document.body.innerHTML).not.toContain('object-cover');
		expect(all('[data-edit-tile] img').every((img) => img.className.includes('object-contain'))).toBe(true);
	});
});
