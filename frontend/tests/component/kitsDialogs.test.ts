// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { makeItem, makeKit } from '$lib/kits/fixtures';

const calls = {
	listKits: vi.fn(),
	getKit: vi.fn(),
	createKit: vi.fn(),
	addKitItems: vi.fn(),
	deleteKit: vi.fn(),
	getKitUsage: vi.fn()
};

vi.mock('$lib/services/api/index', async (importOriginal) => {
	const original = await importOriginal<typeof import('$lib/services/api/index')>();
	for (const [name, fn] of Object.entries(calls)) (original.api as Record<string, unknown>)[name] = (...args: unknown[]) => fn(...args);
	return original;
});

const { default: KitComposeDialog } = await import('$lib/components/kits/KitComposeDialog.svelte');
const { default: KitLoadDialog } = await import('$lib/components/kits/KitLoadDialog.svelte');
const { default: KitDetailPane } = await import('$lib/components/kits/KitDetailPane.svelte');
const { default: ConfirmHost } = await import('$lib/components/modals/ConfirmHost.svelte');
const { resetKitDirectory } = await import('$lib/stores/kits');

const mara = makeKit('Mara', 'mara', [makeItem('image', 'face')]);
const summary = { ...mara, items: undefined };
const page = { success: true, data: { kits: [summary], total: 1, page: 1, page_size: 6, labels: [] } };
const conflict = { response: { status: 409, data: { detail: { success: false, error: 'kit_path_taken', message: "'mara' is already used, try 'mara2'", suggestion: 'mara2' } } } };
const picks = [{ key: 'a', kind: 'image' as const, label: 'Mara, rain', thumbnail: null, mediaRef: 'up_1', text: null }];

let mounted: ReturnType<typeof mount>[] = [];

function mountDialog(component: unknown, props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	mounted.push(mount(component as never, { target, props }));
	flushSync();
	return target;
}

async function settle() {
	for (let i = 0; i < 5; i++) {
		await Promise.resolve();
		flushSync();
	}
}

const escape = () => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
const dialog = () => document.querySelector<HTMLElement>('[role="dialog"], [role="alertdialog"]');
const button = (label: string) => Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.trim().startsWith(label));

beforeEach(() => {
	Object.values(calls).forEach((fn) => fn.mockReset());
	calls.listKits.mockResolvedValue(page);
	resetKitDirectory();
});

afterEach(() => {
	mounted.forEach((component) => unmount(component));
	mounted = [];
	document.body.innerHTML = '';
});

describe('new kit dialog', () => {
	it('puts focus inside, closes on Escape, and shows the server suggestion on a 409', async () => {
		const onClose = vi.fn();
		mountDialog(KitComposeDialog, { picks: [], initialName: 'Mara', onDone: vi.fn(), onClose });
		await vi.waitFor(() => expect(dialog()?.contains(document.activeElement)).toBe(true));
		calls.createKit.mockRejectedValue(conflict);
		button('Create')?.click();
		await settle();
		expect(dialog()?.textContent).toContain('already used');
		expect(dialog()?.textContent).toContain('mara2');
		escape();
		await settle();
		expect(onClose).toHaveBeenCalled();
	});

	it('keeps a kit that was created when a text item failed, and adds the rest to it on retry', async () => {
		const onDone = vi.fn();
		const onClose = vi.fn();
		const text = [{ key: 't', kind: 'text' as const, label: 'Note', thumbnail: null, mediaRef: null, text: 'warm' }];
		mountDialog(KitComposeDialog, { picks: text, initialName: 'Mara', onDone, onClose });
		calls.createKit.mockResolvedValue({ success: false, message: 'Disk full', partial: { created: mara, pending: [{ kind: 'text', name: 'text1', text: 'warm' }], message: 'Disk full' } });
		calls.getKit.mockResolvedValue({ success: true, data: mara });
		button('Create')?.click();
		await settle();
		expect(dialog()?.textContent).toContain('Mara was created. Not added: text1');
		expect(onClose).not.toHaveBeenCalled();
		calls.addKitItems.mockResolvedValue({ success: true, data: mara });
		button('Create')?.click();
		await settle();
		expect(calls.createKit).toHaveBeenCalledTimes(1);
		expect(calls.addKitItems).toHaveBeenCalledWith(mara.id, [{ kind: 'text', name: 'text1', text: 'warm' }]);
		expect(onDone).toHaveBeenCalled();
	});
});

describe('add to kit dialog', () => {
	it('opens with focus inside, shows the 409 message when adding fails, and closes on Escape', async () => {
		const onClose = vi.fn();
		mountDialog(KitComposeDialog, { picks, initialName: 'Mara', onDone: vi.fn(), onClose });
		await vi.waitFor(() => expect(dialog()?.contains(document.activeElement)).toBe(true));
		expect(dialog()?.textContent).toContain('Add 1 item to a kit');
		calls.createKit.mockRejectedValue(conflict);
		button('Create kit')?.click();
		await settle();
		expect(dialog()?.textContent).toContain('already used');
		escape();
		await settle();
		expect(onClose).toHaveBeenCalled();
	});
});

describe('load dialog', () => {
	it('closes on Escape and takes focus', async () => {
		const onClose = vi.fn();
		mountDialog(KitLoadDialog, { onLoad: vi.fn(), onClose });
		await vi.waitFor(() => expect(dialog()?.contains(document.activeElement)).toBe(true));
		escape();
		await settle();
		expect(onClose).toHaveBeenCalled();
	});

	it('stays open and names the kit when it no longer exists (404)', async () => {
		const onClose = vi.fn();
		const onLoad = vi.fn();
		calls.getKit.mockRejectedValue({ response: { status: 404 } });
		mountDialog(KitLoadDialog, { onLoad, onClose });
		await vi.waitFor(() => expect(document.querySelector('[data-kit-load-list] [role="option"]')).not.toBeNull());
		document.querySelector<HTMLElement>('[data-kit-load-list] [role="option"]')!.click();
		await settle();
		button('Load')?.click();
		await settle();
		expect(document.querySelector('[data-kit-load-problems]')?.textContent).toContain('Mara no longer exists');
		expect(onClose).not.toHaveBeenCalled();
		expect(onLoad).not.toHaveBeenCalled();
	});

	it('stays open and says so on a network error', async () => {
		const onClose = vi.fn();
		calls.getKit.mockRejectedValue(new Error('Network Error'));
		mountDialog(KitLoadDialog, { onLoad: vi.fn(), onClose });
		await vi.waitFor(() => expect(document.querySelector('[data-kit-load-list] [role="option"]')).not.toBeNull());
		document.querySelector<HTMLElement>('[data-kit-load-list] [role="option"]')!.click();
		await settle();
		button('Load')?.click();
		await settle();
		expect(document.querySelector('[data-kit-load-problems]')?.textContent).toContain('could not be reached');
		expect(onClose).not.toHaveBeenCalled();
	});

	it('loads what it can, says what it could not, and stays open', async () => {
		const other = makeKit('Old harbour', 'old_harbour', [makeItem('image', 'lighthouse')]);
		calls.listKits.mockResolvedValue({ success: true, data: { kits: [{ ...mara, items: undefined }, { ...other, items: undefined }], total: 2, page: 1, page_size: 6, labels: [] } });
		calls.getKit.mockImplementation(async (id: string) => {
			if (id === mara.id) return { success: true, data: mara };
			throw { response: { status: 404 } };
		});
		const onLoad = vi.fn();
		const onClose = vi.fn();
		mountDialog(KitLoadDialog, { onLoad, onClose });
		await vi.waitFor(() => expect(document.querySelectorAll('[data-kit-load-list] [role="option"]')).toHaveLength(2));
		for (const option of Array.from(document.querySelectorAll<HTMLElement>('[data-kit-load-list] [role="option"]'))) {
			option.click();
			await settle();
		}
		button('Load')?.click();
		await settle();
		expect(onLoad).toHaveBeenCalledTimes(1);
		expect(onLoad.mock.calls[0][0].map((kit: { id: string }) => kit.id)).toEqual([mara.id]);
		expect(onClose).not.toHaveBeenCalled();
		await vi.waitFor(() => {
			flushSync();
			expect(document.querySelector('[data-kit-load-problems]')?.textContent).toContain('Some kits were loaded');
		});
		expect(document.querySelector('[data-kit-load-problems]')?.textContent).toContain('Old harbour no longer exists');
	});
});

describe('delete kit confirm', () => {
	it('lists where the kit is used, takes focus, closes on Escape without deleting', async () => {
		calls.getKit.mockResolvedValue({ success: true, data: mara });
		calls.getKitUsage.mockResolvedValue({ success: true, data: { sessions: 2, films: 1 } });
		mountDialog(ConfirmHost, {});
		mountDialog(KitDetailPane, { kitId: mara.id, onBack: vi.fn(), onDeleted: vi.fn(), onOpenKit: vi.fn() });
		await vi.waitFor(() => expect(document.querySelector('[aria-label="Delete kit"]')).not.toBeNull());
		document.querySelector<HTMLElement>('[aria-label="Delete kit"]')!.click();
		await vi.waitFor(() => expect(dialog()).not.toBeNull());
		expect(dialog()?.textContent).toContain('2 saved sessions and 1 Director film');
		expect(dialog()?.contains(document.activeElement)).toBe(true);
		escape();
		await settle();
		expect(calls.deleteKit).not.toHaveBeenCalled();
	});

	it('deletes after Delete kit is pressed', async () => {
		calls.getKit.mockResolvedValue({ success: true, data: mara });
		calls.getKitUsage.mockResolvedValue({ success: true, data: { sessions: 0, films: 0 } });
		calls.deleteKit.mockResolvedValue({ success: true, data: { id: mara.id } });
		const onDeleted = vi.fn();
		mountDialog(ConfirmHost, {});
		mountDialog(KitDetailPane, { kitId: mara.id, onBack: vi.fn(), onDeleted, onOpenKit: vi.fn() });
		await vi.waitFor(() => expect(document.querySelector('[aria-label="Delete kit"]')).not.toBeNull());
		document.querySelector<HTMLElement>('[aria-label="Delete kit"]')!.click();
		await vi.waitFor(() => expect(dialog()).not.toBeNull());
		button('Delete kit')?.click();
		await vi.waitFor(() => expect(onDeleted).toHaveBeenCalled());
		expect(calls.deleteKit).toHaveBeenCalledWith(mara.id);
	});
});
