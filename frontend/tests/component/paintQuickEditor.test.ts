import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createClassComponent } from 'svelte/legacy';
import { tick } from 'svelte';
import { installFakeCanvas } from './helpers/fakeCanvas';

class StubResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}
vi.stubGlobal('ResizeObserver', StubResizeObserver);

const uploadMedia = vi.fn();
const listUploads = vi.fn();
const fetchSpy = vi.fn(async () => new Response('{}'));
vi.stubGlobal('fetch', fetchSpy);

vi.mock('$lib/media/loadImage', () => ({
	loadImage: async () => {
		const canvas = document.createElement('canvas');
		canvas.width = 8;
		canvas.height = 6;
		return Object.assign(canvas, { naturalWidth: 8, naturalHeight: 6 });
	}
}));

vi.mock('$lib/paint/flatten', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/paint/flatten')>()),
	canvasToPngFile: async (_canvas: unknown, name: string) => new File(['png'], name, { type: 'image/png' })
}));

vi.mock('$lib/services/api/index', () => ({
	api: {
		uploadMedia: (...args: unknown[]) => uploadMedia(...args),
		listUploads: (...args: unknown[]) => listUploads(...args),
		listFilters: async () => ({ filters: [], ops: [], groups: [] })
	}
}));

const { default: MediaEditors } = await import('$lib/media/editors/MediaEditors.svelte');
const { PaintSession } = await import('$lib/paint/session');
const { createImageEditorApi, pluginTools, paintActions, imageSources } = await import('$lib/paint/registries');

const source = {
	url: '/api/media/uploads/portrait.png',
	kind: 'image' as const,
	fileName: 'portrait.png',
	itemId: null,
	storedPath: 'uploads/portrait.png',
	width: 8,
	height: 6
};

const draw = { width: 32, height: 24, background: 'white' as const, pen: null, scribble: false };

let restoreCanvas: () => void;
let session: InstanceType<typeof PaintSession>;
let instance: { $destroy: () => void } | null = null;
const plugin = createImageEditorApi();

async function settle() {
	for (let i = 0; i < 8; i += 1) {
		await tick();
		await new Promise((resolve) => setTimeout(resolve, 0));
	}
}

function mount(props: Record<string, unknown> = {}) {
	const onClose = vi.fn();
	const onResult = vi.fn();
	const target = document.createElement('div');
	document.body.appendChild(target);
	instance = createClassComponent({
		component: MediaEditors as never,
		target,
		props: {
			request: { kind: 'paint', source, itemIndex: null },
			scope: 'field',
			onClose,
			onResult,
			...props
		}
	});
	return { onClose, onResult };
}

async function opened() {
	await vi.waitFor(() => expect(document.body.querySelector('canvas[aria-label="Drawing canvas"]')).not.toBeNull(), { timeout: 8000 });
	await settle();
}

const buttons = () => Array.from(document.body.querySelectorAll('button'));
const buttonByText = (text: string) => buttons().find((button) => (button.textContent || '').trim() === text);
const byLabel = (label: string) => document.body.querySelector<HTMLElement>(`[aria-label="${label}"]`);

function dotTool() {
	return {
		id: 'dot',
		label: 'Dot',
		icon: 'M0 0',
		group: 'paint',
		pointerDown() {},
		pointerUp(host: any) {
			const layer = host.activeLayer;
			const before = host.snapshotLayer(layer);
			const context = layer.canvas.getContext('2d');
			context.fillStyle = 'rgb(255, 0, 0)';
			context.fillRect(0, 0, 2, 2);
			host.commitPixels('Dot', layer, { x: 0, y: 0, width: 2, height: 2 }, before);
		}
	};
}

async function stroke() {
	const off = plugin.registerTool(dotTool());
	await settle();
	const canvas = document.body.querySelector<HTMLCanvasElement>('canvas[aria-label="Drawing canvas"]')!;
	canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 300, right: 400, bottom: 300, x: 0, y: 0, toJSON: () => ({}) });
	session.resize(400, 300, 1);
	session.setTool('dot');
	canvas.dispatchEvent(new PointerEvent('pointerdown', { button: 0, pointerId: 1, clientX: 196, clientY: 148, bubbles: true }));
	canvas.dispatchEvent(new PointerEvent('pointermove', { pointerId: 1, clientX: 204, clientY: 150, bubbles: true }));
	canvas.dispatchEvent(new PointerEvent('pointerup', { pointerId: 1, clientX: 204, clientY: 150, bubbles: true }));
	await settle();
	off();
}

beforeEach(() => {
	restoreCanvas = installFakeCanvas();
	HTMLCanvasElement.prototype.setPointerCapture = vi.fn();
	const attach = PaintSession.prototype.attach;
	vi.spyOn(PaintSession.prototype, 'attach').mockImplementation(function (this: InstanceType<typeof PaintSession>, ...args: Parameters<typeof attach>) {
		session = this;
		return attach.apply(this, args);
	});
	uploadMedia.mockReset().mockResolvedValue({
		success: true,
		data: { path: 'uploads/p.png', relative_path: 'uploads/p.png', filename: 'p.png', size: 3, width: 8, height: 6, url: '/api/media/uploads/p.png' }
	});
	listUploads.mockReset().mockResolvedValue({ success: false });
	fetchSpy.mockClear();
});

afterEach(async () => {
	instance?.$destroy();
	instance = null;
	await settle();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
	restoreCanvas();
});

describe('quick paint modal', () => {
	it('opens on the picture with every registered tool and is the active editor while open', async () => {
		mount();
		await opened();

		expect(document.body.querySelector('[role="dialog"]')).not.toBeNull();
		expect(document.body.textContent).toContain('Edit image');
		const rail = document.body.querySelector('[aria-label="Tools"]')!;
		const labels = Array.from(rail.querySelectorAll('button')).map((button) => button.getAttribute('aria-label'));
		for (const tool of session.snapshot().tools) expect(labels).toContain(tool.label);
		expect(session.snapshot().width).toBe(8);
		expect(plugin.isOpen()).toBe(true);
		expect(buttonByText('Save')!.disabled).toBe(false);

		instance!.$destroy();
		instance = null;
		await settle();
		expect(plugin.isOpen()).toBe(false);
	});

	it('opens empty for Draw at the chosen size', async () => {
		mount({ request: { kind: 'paint', source: { url: '', kind: 'image', fileName: 'Untitled drawing' }, itemIndex: null, draw } });
		await opened();

		expect(document.body.textContent).toContain('Draw');
		expect(session.snapshot().width).toBe(32);
		expect(session.snapshot().height).toBe(24);
		expect(session.snapshot().dirty).toBe(false);
	});

	it('paints, applies, uploads as a user upload and returns the item without keeping a mask', async () => {
		const { onClose, onResult } = mount();
		await opened();
		await stroke();
		expect(session.snapshot().dirty).toBe(true);

		buttonByText('Save')!.click();
		await settle();

		expect(uploadMedia).toHaveBeenCalledTimes(1);
		const [file, purpose] = uploadMedia.mock.calls[0];
		expect(purpose).toBe('user_upload');
		expect((file as File).type).toBe('image/png');
		expect(onResult).toHaveBeenCalledTimes(1);
		const [result] = onResult.mock.calls[0];
		expect(result).toMatchObject({ type: 'item', replaced: false, keepMask: false });
		expect(result.item.media_type).toBe('image');
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('cancels a clean session straight away and asks before throwing painted work away', async () => {
		const clean = mount();
		await opened();
		buttonByText('Cancel')!.click();
		await settle();
		expect(clean.onClose).toHaveBeenCalledTimes(1);
		instance!.$destroy();

		const dirty = mount();
		await opened();
		await stroke();
		buttonByText('Cancel')!.click();
		await settle();
		expect(dirty.onClose).not.toHaveBeenCalled();
		expect(document.body.querySelector('[role="alertdialog"]')).not.toBeNull();
		buttonByText('Discard')!.click();
		await settle();
		expect(dirty.onClose).toHaveBeenCalledTimes(1);
		expect(dirty.onResult).not.toHaveBeenCalled();
		expect(uploadMedia).not.toHaveBeenCalled();
	});

	it('discards on Escape without uploading', async () => {
		const { onClose, onResult } = mount();
		await opened();
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
		await settle();
		expect(onClose).toHaveBeenCalled();
		expect(onResult).not.toHaveBeenCalled();
		expect(uploadMedia).not.toHaveBeenCalled();
	});

	it('creates no edit and makes no edits request', async () => {
		mount();
		await opened();
		await stroke();
		buttonByText('Save')!.click();
		await settle();

		const urls = fetchSpy.mock.calls.map((call) => String((call as unknown[])[0]));
		expect(urls.filter((url) => url.includes('/api/edits'))).toEqual([]);
		expect(document.body.querySelector('[data-image-editor-tab]')).toBeNull();
	});

	it('keeps an existing mask while the geometry is untouched and clears it after a flip', async () => {
		const kept = mount({ existingMaskUrl: '/api/media/uploads/mask-1.png' });
		await opened();
		expect(document.body.textContent).toContain('Inpaint mask is kept');
		buttonByText('Save')!.click();
		await settle();
		expect(kept.onResult.mock.calls[0][0]).toMatchObject({ type: 'item', keepMask: true });
		instance!.$destroy();
		document.body.innerHTML = '';

		const cleared = mount({ existingMaskUrl: '/api/media/uploads/mask-1.png' });
		await opened();
		byLabel('Flip horizontally')!.click();
		await settle();
		expect(document.body.textContent).toContain('Inpaint mask will be cleared');
		buttonByText('Save')!.click();
		await settle();
		expect(cleared.onResult.mock.calls[0][0]).toMatchObject({ type: 'item', keepMask: false });
	});

	it('shows plugin tools, actions and image sources and lets addImage reach its session', async () => {
		const run = vi.fn();
		const pick = vi.fn(async () => null);
		const offs = [
			plugin.registerTool({ id: 'zigzag', label: 'Zigzag', icon: 'M0 0', group: 'shapes', key: 'Z' }),
			plugin.registerAction({ id: 'stamp', label: 'Stamp', run }),
			plugin.registerImageSource({ id: 'stock', label: 'Stock photos', pick })
		];
		try {
			mount();
			await opened();

			expect(byLabel('Zigzag')).not.toBeNull();
			buttonByText('Stamp')!.click();
			await settle();
			expect(run).toHaveBeenCalledTimes(1);
			expect(typeof run.mock.calls[0][0].addImage).toBe('function');

			byLabel('Open another image')!.click();
			await settle();
			const menu = document.body.querySelector('[role="menu"]')!;
			expect(menu.textContent).toContain('Stock photos');

			const before = session.snapshot().layers.length;
			const canvas = document.createElement('canvas');
			canvas.width = 4;
			canvas.height = 4;
			expect(plugin.addImage(canvas, 'overlay')).toBe(true);
			await settle();
			expect(session.snapshot().layers.length).toBe(before + 1);
		} finally {
			offs.forEach((off) => off());
		}
		expect(pluginTools.list().some((tool) => tool.id === 'zigzag')).toBe(false);
		expect(paintActions.list().some((action) => action.id === 'stamp')).toBe(false);
		expect(imageSources.list().some((entry) => entry.id === 'stock')).toBe(false);
		instance!.$destroy();
		instance = null;
		await settle();
		expect(plugin.addImage(document.createElement('canvas'), 'late')).toBe(false);
	});
});
