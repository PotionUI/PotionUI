import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { settleFlush } from '../helpers/settle';
import { stubClientWidth } from '../helpers/stubClientWidth';
import { tabsStore } from '$lib/stores/tabs';

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

function edits(total: number) {
	const shown = Math.min(total, 12);
	return {
		success: true,
		data: {
			edits: Array.from({ length: shown }, (_, index) => ({
				id: `e${index + 1}`,
				tool: 'image',
				name: null,
				source: { kind: 'library', id: `s${index + 1}`, name: `picture-${index + 1}.png` },
				thumbnail_upload_id: null,
				revision: 2,
				created_at: '2026-11-16T10:00:00Z',
				updated_at: '2026-11-16T10:00:00Z'
			})),
			total,
			limit: 12,
			offset: 0
		}
	};
}

function render(props: Record<string, unknown> = {}) {
	const onpick = vi.fn();
	const onfile = vi.fn();
	const view = mountComponent(ImageStartScreen, { tabId: 'tab-self', onpick, onfile, ...props });
	const q = <T extends Element = HTMLElement>(selector: string) => view.target.querySelector<T>(selector);
	const all = (selector: string) => Array.from(view.target.querySelectorAll<HTMLElement>(selector));
	return { view, q, all, onpick, onfile };
}

describe('ImageStartScreen', () => {
	let restoreWidth = () => {};

	beforeEach(() => {
		tabsStore.reset();
		restoreWidth = stubClientWidth(1000);
		mocks.listEdits.mockReset().mockResolvedValue(edits(27));
		mocks.getEdit.mockReset();
		mocks.getLibraryItem.mockReset().mockResolvedValue({ success: true, data: { id: 's1', url: '/api/media/uploads/s1.png' } });
		mocks.getGenerationHistory.mockReset();
	});

	afterEach(() => {
		restoreWidth();
		tabsStore.reset();
	});

	it('is the shared page empty state with the four sources in the Video editor order', async () => {
		mocks.listEdits.mockResolvedValue(edits(0));
		const { q, all } = render();
		await settleFlush();
		expect(q('[data-testid="tool-tab-empty"]')).not.toBeNull();
		const empty = q('[data-empty-state="page"]')!;
		expect(empty.querySelector('[data-empty-state-title]')?.textContent).toBe('Open a picture to edit');
		expect(empty.querySelector('[data-empty-state-description]')?.textContent).toContain('Paste or drop one anywhere.');
		expect(all('[data-start-source]').map((node) => node.dataset.startSource)).toEqual(['browse', 'history', 'library', 'kits']);
		expect(all('[data-start-source]').map((node) => node.textContent?.trim())).toEqual(['Upload file', 'History', 'Library', 'Kits']);
		expect(all('[data-start-source]').every((node) => empty.contains(node))).toBe(true);
	});

	it('shows no band, no tiles and no search any more', async () => {
		const { q } = render();
		await settleFlush();
		expect(q('[data-start-panel]')).toBeNull();
		expect(q('[data-edit-tile]')).toBeNull();
		expect(q('[data-edits-list]')).toBeNull();
		expect(q('input[aria-label="Search edits by name"]')).toBeNull();
		expect(mocks.getGenerationHistory).not.toHaveBeenCalled();
	});

	it('without edits there is no Recent edits button', async () => {
		mocks.listEdits.mockResolvedValue(edits(0));
		const { q } = render();
		await settleFlush();
		expect(q('[data-start-recent]')).toBeNull();
	});

	it('with edits a Recent edits button with the count follows the four sources', async () => {
		const { q, all } = render();
		await settleFlush();
		expect(mocks.listEdits).toHaveBeenCalledWith({ tool: 'image', limit: 1, offset: 0 });
		const recent = q('[data-start-recent]')!;
		expect(recent.textContent?.replace(/\s+/g, ' ').trim()).toBe('Recent edits 27');
		expect(recent.querySelector('[data-recent-count]')?.className).toContain('font-mono');
		const row = q('[data-start-sources]')!;
		expect(row.lastElementChild).toBe(recent);
		expect(all('[data-start-sources] button')).toHaveLength(5);
	});

	it('the Recent edits button opens the dialog and closing it brings back the screen', async () => {
		const { q } = render();
		await settleFlush();
		expect(document.body.querySelector('[role="dialog"]')).toBeNull();
		q<HTMLButtonElement>('[data-start-recent]')!.click();
		await settleFlush();
		const dialog = document.body.querySelector('[role="dialog"]')!;
		expect(dialog.textContent).toContain('Recent edits');
		expect(dialog.querySelectorAll('[data-edit-tile]')).toHaveLength(12);
		dialog.querySelector<HTMLButtonElement>('button[aria-label="Close modal"]')!.click();
		await settleFlush();
		expect(document.body.querySelector('[role="dialog"]')).toBeNull();
		expect(q('[data-start-recent]')).not.toBeNull();
	});

	it('names the paste and drop in the description and keeps the same screen on a phone', async () => {
		const { q } = render({ phone: true });
		await settleFlush();
		expect(q('[data-empty-state-description]')?.textContent).toContain('Paste or drop one anywhere.');
		expect(q('[data-start-sources]')?.className).toContain('flex-wrap');
	});

	it('shows the busy spinner and the error under the buttons', async () => {
		const { q } = render({ busy: true, error: 'Could not open that picture.' });
		await settleFlush();
		expect(q('[data-empty-state="page"] [role="alert"]')?.textContent).toBe('Could not open that picture.');
		expect(q('[data-empty-state="page"] [role="status"], [data-empty-state="page"] svg.animate-spin')).not.toBeNull();
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
		expect(overlay.className).toContain('pointer-events-none');
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
		Object.defineProperty(inner, 'relatedTarget', { value: q('[data-start-screen]') });
		q('[data-start-sources]')!.dispatchEvent(inner);
		flushSync();
		expect(q('[data-drop-overlay]')).not.toBeNull();
		root.dispatchEvent(new Event('dragleave', { bubbles: true }));
		flushSync();
		expect(q('[data-drop-overlay]')).toBeNull();
	});
});
