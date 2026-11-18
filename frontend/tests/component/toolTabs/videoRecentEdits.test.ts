import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { get } from 'svelte/store';
import { mountComponent } from '../helpers/mount';
import { stubClientWidth } from '../helpers/stubClientWidth';
import { settleFlush } from '../helpers/settle';
import { tabsStore } from '$lib/stores/tabs';
import { isToolTab } from '$lib/types/tabs';
import { activeConfirm, settleConfirm } from '$lib/stores/confirm';
import { readVideoState, writeVideoState } from '$lib/tool-tabs/video/videoState';

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
const { default: VideoStartScreen } = await import('$lib/components/tool-tabs/video/VideoStartScreen.svelte');

const SHAPES = [
	{ duration_seconds: 12.4, width: 1920, height: 1080 },
	{ duration_seconds: 75, width: 1080, height: 1920 },
	{ duration_seconds: 3600, width: 1000, height: 1000 }
];

function clipEdit(n: number) {
	return {
		id: `v${n}`,
		tool: 'video',
		name: n === 1 ? 'Dock cut' : null,
		source: { kind: n % 2 ? 'library' : 'history', id: `c${n}`, name: `clip-${n}.mp4` },
		thumbnail_upload_id: null,
		clip: SHAPES[(n - 1) % SHAPES.length],
		revision: 3,
		created_at: '2026-11-16T10:00:00Z',
		updated_at: '2026-11-16T10:00:00Z'
	};
}

function clipList(total: number) {
	return { success: true, data: { edits: Array.from({ length: total }, (_, index) => clipEdit(index + 1)), total, limit: 12, offset: 0 } };
}

function render(props: Record<string, unknown> = {}) {
	const onclose = vi.fn();
	const view = mountComponent(RecentEditsDialog, { tabId: 'tab-self', tool: 'video', onclose, ...props });
	const q = <T extends Element = HTMLElement>(selector: string) => document.body.querySelector<T>(selector);
	const all = (selector: string) => Array.from(document.body.querySelectorAll<HTMLElement>(selector));
	return { view, q, all, onclose };
}

function box(node: HTMLElement) {
	const wrapper = node.closest<HTMLElement>('[data-tile-box]')!;
	const width = Number(/(\d+(?:\.\d+)?)px/.exec(wrapper.style.flex)![1]);
	const media = wrapper.querySelector<HTMLElement>('[data-edit-open]')!;
	const height = Number(/(\d+(?:\.\d+)?)px/.exec(media.style.height)![1]);
	return { width, height };
}

describe('RecentEditsDialog for clips', () => {
	let restoreWidth = () => {};

	beforeEach(() => {
		tabsStore.reset();
		restoreWidth = stubClientWidth(1000);
		mocks.listEdits.mockReset().mockResolvedValue(clipList(3));
		mocks.getEdit.mockReset();
		mocks.renameEdit.mockReset();
		mocks.duplicateEdit.mockReset();
		mocks.deleteEdit.mockReset();
		mocks.getLibraryItem.mockReset().mockResolvedValue({
			success: true,
			data: {
				id: 'c1',
				filename: 'c1.mp4',
				original_filename: 'clip-1.mp4',
				media_type: 'video',
				url: '/api/media/uploads/c1.mp4',
				thumbnail_medium: '/api/media/uploads/c1.mp4?size=medium',
				width: 1920,
				height: 1080,
				duration_seconds: 12.4
			}
		});
	});

	afterEach(() => {
		restoreWidth();
		tabsStore.reset();
	});

	it('asks for clip edits only', async () => {
		render();
		await settleFlush();
		expect(mocks.listEdits).toHaveBeenCalledWith({ tool: 'video', limit: 12, offset: 0 });
	});

	it('shows each clip with a mono duration badge', async () => {
		const { all, q } = render();
		await settleFlush();
		expect(all('[data-edit-tile]')).toHaveLength(3);
		const badges = all('[data-clip-duration]');
		expect(badges.map((node) => node.textContent?.trim())).toEqual(['0:12', '1:15', '1:00:00']);
		expect(badges.every((node) => node.className.includes('font-mono') && node.className.includes('tabular-nums'))).toBe(true);
		expect(q('[data-edit-tile="v1"] [data-edit-name]')?.textContent?.trim()).toBe('Dock cut');
		expect(q('[data-edit-tile="v2"] [data-edit-name]')?.textContent?.trim()).toBe('clip-2');
	});

	it('lays the posters out at the clip aspect before anything loads and never crops them', async () => {
		const { all } = render();
		await settleFlush();
		const [wide, tall, square] = all('[data-edit-tile]').map(box);
		expect(wide.width / wide.height).toBeCloseTo(16 / 9, 1);
		expect(tall.width / tall.height).toBeCloseTo(9 / 16, 1);
		expect(square.width / square.height).toBeCloseTo(1, 1);
		expect(document.body.innerHTML).not.toContain('object-cover');
	});

	it('uses the library thumbnail as the poster and a clip frame when the history file has none', async () => {
		const { q } = render();
		await settleFlush();
		expect(q('[data-edit-tile="v1"] img')?.getAttribute('src')).toBe('/api/media/uploads/c1.mp4?size=medium');
		const history = q('[data-edit-tile="v2"] img')!;
		expect(history.getAttribute('src')).toBe('/api/media/files/c2?size=medium');
		history.dispatchEvent(new Event('error'));
		flushSync();
		const frame = q('[data-edit-tile="v2"] video')!;
		expect(frame.getAttribute('src')).toBe('/api/media/files/c2#t=0.1');
		expect(frame.hasAttribute('controls')).toBe(false);
	});

	it('continuing a clip edit restores its source and steps into this tab', async () => {
		const stateIn = writeVideoState({ ...readVideoState(null), tool: 'trim' });
		mocks.getEdit.mockResolvedValue({ success: true, data: { edit: { ...clipEdit(1), revision: 5, state: stateIn } } });
		const tab = tabsStore.openToolTab('video', null);
		tabsStore.setActiveTab(tab);
		const { q, onclose } = render({ tabId: tab });
		await settleFlush();
		(q('[data-edit-tile="v1"] [data-edit-open]') as HTMLElement).click();
		await settleFlush();
		const restored = get(tabsStore).tabs.find((entry) => entry.id === tab);
		expect(restored && isToolTab(restored) && restored.source?.type).toBe('video');
		expect(restored && isToolTab(restored) && restored.source?.item_id).toBe('c1');
		expect(restored && isToolTab(restored) && (restored.state as { edit?: unknown }).edit).toEqual({ id: 'v1', revision: 5 });
		expect(restored && isToolTab(restored) && restored.activeTool).toBe('trim');
		expect(onclose).toHaveBeenCalled();
	});

	it('does not mark a picture tab that holds the same id as open for a clip edit', async () => {
		const other = tabsStore.openToolTab('image', { path: '/m/x.png', url: '/m/x.png', name: 'x.png', item_id: 'z' });
		tabsStore.updateToolTab(other, { state: { v: 1, edit: { id: 'v2', revision: 2 } } });
		const { all } = render();
		await settleFlush();
		expect(all('[data-open-elsewhere]')).toHaveLength(0);
	});

	it('delete says the original clip is not affected', async () => {
		render();
		await settleFlush();
		(document.body.querySelector('[data-edit-tile="v1"] button[aria-label^="Options for"]') as HTMLElement).click();
		flushSync();
		const remove = Array.from(document.body.querySelectorAll<HTMLElement>('[role="menuitem"]')).find((node) => node.textContent?.trim() === 'Delete')!;
		remove.click();
		await settleFlush();
		const request = get(activeConfirm);
		expect(request?.message).toContain('The original clip is not affected.');
		settleConfirm(request!.id, false);
	});
});

describe('VideoStartScreen', () => {
	beforeEach(() => {
		tabsStore.reset();
		mocks.listEdits.mockReset().mockResolvedValue(clipList(3));
	});

	it('is the standard empty state and offers Recent edits with the count of clip edits', async () => {
		const onpick = vi.fn();
		const view = mountComponent(VideoStartScreen, { tabId: 'tab-self', onpick });
		await settleFlush();
		expect(view.target.querySelector('[data-testid="tool-tab-empty"]')).not.toBeNull();
		expect(mocks.listEdits).toHaveBeenCalledWith({ tool: 'video', limit: 1, offset: 0 });
		const recent = view.target.querySelector<HTMLElement>('[data-start-recent]')!;
		expect(recent.textContent).toContain('Recent edits');
		expect(recent.querySelector('[data-recent-count]')?.textContent).toBe('3');
		recent.click();
		await settleFlush();
		expect(document.body.querySelector('[data-recent-edits]')).not.toBeNull();
	});

	it('has no Recent edits button before the first clip edit', async () => {
		mocks.listEdits.mockResolvedValue({ success: true, data: { edits: [], total: 0, limit: 1, offset: 0 } });
		const view = mountComponent(VideoStartScreen, { tabId: 'tab-self', onpick: vi.fn() });
		await settleFlush();
		expect(view.target.querySelector('[data-start-recent]')).toBeNull();
	});
});
