import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
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
		expect(q('[data-empty-state="compact"]')?.textContent).toContain('No edits yet');
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

	it('first use with no History shows the full-page empty state alone', async () => {
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

describe('ImageStartScreen layout', () => {
	const source = readFileSync(resolve(process.cwd(), 'src/lib/components/tool-tabs/image/ImageStartScreen.svelte'), 'utf8');

	beforeEach(() => {
		tabsStore.reset();
		mocks.listEdits.mockReset().mockResolvedValue(list(27));
		mocks.getEdit.mockReset();
		mocks.getLibraryItem
			.mockReset()
			.mockResolvedValue({ success: true, data: { id: 's1', filename: 's1.png', original_filename: 'picture-1.png', media_type: 'image', url: '/api/media/uploads/s1.png' } });
		mocks.getGenerationHistory.mockReset().mockResolvedValue(history());
	});

	afterEach(() => tabsStore.reset());

	it('is full width with no centred max-width container', async () => {
		const { q } = render();
		await settleFlush();
		expect(q('[data-start-screen]')).not.toBeNull();
		expect(q('[class*="max-w-["]')).toBeNull();
		expect(q('aside')).toBeNull();
		expect(q('[data-start-screen]')?.className).not.toMatch(/grid-cols/);
	});

	it('puts the sources band first, with the four sources, above the recent edits', async () => {
		const { q, all } = render();
		await settleFlush();
		const band = q('[data-start-panel]')!;
		const recent = q('[data-recent-edits]')!;
		expect(band.compareDocumentPosition(recent) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(band.querySelector('h2')?.textContent).toBe('Start from a picture');
		expect(band.textContent).toContain('Open one to begin a new edit.');
		expect(Array.from(band.querySelectorAll('[data-start-source]'))).toHaveLength(4);
		expect(all('[data-start-source]').every((node) => band.contains(node))).toBe(true);
		expect(q('[data-start-sources]')?.className).toContain('srcs');
	});

	it('shows the drop and paste hint on the band header line with the keys together', async () => {
		const { q, all } = render();
		await settleFlush();
		const hint = q('[data-start-hint]')!;
		expect(hint.parentElement?.querySelector('h2')).not.toBeNull();
		expect(hint.textContent).toContain('Drop a picture anywhere, or paste');
		expect(hint.className).toContain('whitespace-nowrap');
		expect(all('[data-start-hint] kbd').map((node) => node.textContent)).toEqual(['Ctrl', 'V']);
		const keys = q('[data-start-keys]')!;
		expect(keys.className).toContain('whitespace-nowrap');
		expect(keys.querySelectorAll('kbd')).toHaveLength(2);
	});

	it('leaves the paste hint and keys out on a phone', async () => {
		const { q } = render({ phone: true });
		await settleFlush();
		expect(q('[data-start-hint]')?.textContent).toContain('Drop a picture anywhere');
		expect(q('[data-start-hint]')?.textContent).not.toContain('paste');
		expect(q('[data-start-hint] kbd')).toBeNull();
	});

	it('puts the saved-as-you-go note on the Recent edits header line next to the search', async () => {
		const { q } = render();
		await settleFlush();
		const header = q('[data-recent-edits] h1')!.closest('.rhead')!;
		const note = q('[data-edits-note]')!;
		expect(header.contains(note)).toBe(true);
		expect(header.contains(q('[data-edits-search]'))).toBe(true);
		expect(note.textContent).toContain('Your changes are saved as you go. Edits stay here until you delete them.');
		expect(q('[data-start-panel]')?.textContent).not.toContain('saved as you go');
	});

	it('hides the note on first use, where the empty state says the same', async () => {
		mocks.listEdits.mockResolvedValue(NONE);
		const { q } = render();
		await settleFlush();
		expect(q('[data-edits-note]')).toBeNull();
		expect(q('[data-edits-search]')).toBeNull();
	});

	it('27 edits: twelve tiles, the count and numbered pages', async () => {
		const { q, all } = render();
		await settleFlush();
		expect(all('[data-edits-list] [data-edit-tile]')).toHaveLength(12);
		expect(q('[data-recent-edits]')?.textContent).toContain('27');
		const pages = all('nav[aria-label="Pagination"] button').map((node) => node.textContent?.trim()).filter((text) => /^\d+$/.test(text ?? ''));
		expect(pages).toEqual(['1', '2', '3']);
	});

	it('2 edits: both tiles and no pagination', async () => {
		mocks.listEdits.mockResolvedValue(list(2));
		const { q, all } = render();
		await settleFlush();
		expect(all('[data-edits-list] [data-edit-tile]')).toHaveLength(2);
		expect(q('[data-recent-edits]')?.textContent).toContain('2');
		expect(q('nav[aria-label="Pagination"]')).toBeNull();
	});

	it('0 edits: the band, the compact empty state and one row of six History pictures at most', async () => {
		mocks.listEdits.mockResolvedValue(NONE);
		const now = new Date().toISOString();
		const picks = [
			{
				id: 'g1',
				created_at: now,
				files: Array.from({ length: 8 }, (_, index) => ({ id: 20 + index, file_path: `outputs/d/g1/p${index}.png`, file_type: 'image', is_final: true, created_at: now }))
			}
		];
		mocks.getGenerationHistory.mockResolvedValue({ success: true, data: { generations: picks } });
		const { q, all } = render();
		await settleFlush();
		expect(q('[data-start-panel]')).not.toBeNull();
		expect(q('[data-empty-state="compact"]')).not.toBeNull();
		expect(all('[data-recent-history] [data-edit-tile]').length).toBeLessThanOrEqual(6);
		expect(all('[data-recent-history] [data-edit-tile]').length).toBeGreaterThan(0);
	});

	it('grid steps are 2, 3, 4 and 6 columns by width, so twelve fill whole rows', () => {
		const steps = [...source.matchAll(/@container start \(min-width: (\d+)px\) \{\s*\.edit-grid \{\s*grid-template-columns: repeat\((\d+)/g)].map((match) => [Number(match[1]), Number(match[2])]);
		expect(steps).toEqual([
			[560, 3],
			[820, 4],
			[1240, 6]
		]);
		expect(source).toMatch(/\.edit-grid \{\s*display: grid;\s*grid-template-columns: repeat\(2, /);
		for (const columns of [2, 3, 4, 6]) expect(12 % columns).toBe(0);
		expect(source).not.toContain('auto-fill');
	});

	it('the sources go 2x2 and compact on a narrow band', () => {
		expect(source).toMatch(/@container band \(max-width: 880px\) \{\s*\.srcs \{\s*grid-template-columns: repeat\(2, /);
		expect(source).toMatch(/@container band \(max-width: 520px\) \{[\s\S]*\.band-sub,\s*\.band-hint \{\s*display: none;[\s\S]*flex-direction: column;/);
		expect(source).toMatch(/\.srcs \{\s*display: grid;\s*grid-template-columns: repeat\(4, /);
	});

	it('page padding follows the sibling pages and the note wraps below the search when narrow', () => {
		expect(source).toContain('p-6 dot-grid max-md:p-3');
		expect(source).toMatch(/\.rnote \{\s*order: 3;\s*flex: 1 1 100%;/);
		expect(source).toMatch(/@container start \(min-width: 960px\) \{\s*\.rnote \{\s*order: 1;/);
	});

	it('dragging shows the drop overlay over the whole screen and dropping starts an edit', async () => {
		const { q, onfile } = render();
		await settleFlush();
		const root = q('[data-testid="tool-tab-empty"]')!;
		expect(q('[data-drop-overlay]')).toBeNull();
		root.dispatchEvent(new Event('dragover', { bubbles: true, cancelable: true }));
		flushSync();
		const overlay = q('[data-drop-overlay]')!;
		expect(overlay.textContent).toContain('Drop a picture to start an edit');
		expect(overlay.textContent).toContain('Release it anywhere on this screen.');
		expect(overlay.className).toContain('absolute inset-0');
		expect(overlay.className).toContain('pointer-events-none');
		expect(q('[data-start-hint]')?.textContent).toContain('Release to start an edit');
		const file = new File(['x'], 'a.png', { type: 'image/png' });
		const drop = new Event('drop', { bubbles: true, cancelable: true });
		Object.defineProperty(drop, 'dataTransfer', { value: { files: [file] } });
		root.dispatchEvent(drop);
		flushSync();
		expect(onfile).toHaveBeenCalledWith(file);
		expect(q('[data-drop-overlay]')).toBeNull();
	});

	it('leaving the screen clears the overlay but moving between its children does not', async () => {
		const { q } = render();
		await settleFlush();
		const root = q('[data-testid="tool-tab-empty"]')!;
		root.dispatchEvent(new Event('dragover', { bubbles: true, cancelable: true }));
		flushSync();
		const inner = new Event('dragleave', { bubbles: true });
		Object.defineProperty(inner, 'relatedTarget', { value: q('[data-start-panel]') });
		q('[data-start-sources]')!.dispatchEvent(inner);
		flushSync();
		expect(q('[data-drop-overlay]')).not.toBeNull();
		root.dispatchEvent(new Event('dragleave', { bubbles: true }));
		flushSync();
		expect(q('[data-drop-overlay]')).toBeNull();
	});
});

