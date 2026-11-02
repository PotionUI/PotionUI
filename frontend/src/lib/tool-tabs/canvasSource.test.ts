import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

const uploadMedia = vi.fn();
const error = vi.fn();
vi.mock('$lib/services/api/index', () => ({ api: { uploadMedia: (...args: unknown[]) => uploadMedia(...args) } }));
vi.mock('$lib/stores/toast', () => ({ toasts: { error: (...args: unknown[]) => error(...args) } }));

const { tabsStore } = await import('$lib/stores/tabs');
const { isToolTab } = await import('$lib/types/tabs');
const { setActiveEditor } = await import('$lib/components/imageEditor/registries');
const { createHostImageEditorApi, openCanvasInImageEditor } = await import('./canvasSource');

function canvasOf(blob: Blob | null) {
	return { toBlob: (done: (value: Blob | null) => void) => done(blob) } as unknown as HTMLCanvasElement;
}

const UPLOAD = {
	success: true,
	data: { path: '/s/u.png', relative_path: 'uploads/u.png', url: '/api/media/uploads/u.png', filename: 'u.png', size: 3 }
};

const toolTabs = () => get(tabsStore).tabs.filter(isToolTab);

beforeEach(() => {
	Object.defineProperty(globalThis, 'localStorage', {
		configurable: true,
		value: { getItem: () => null, setItem: () => undefined, removeItem: () => undefined }
	});
	uploadMedia.mockReset().mockResolvedValue(UPLOAD);
	error.mockReset();
	setActiveEditor(null);
	tabsStore.reset();
});

describe('openCanvasInImageEditor', () => {
	it('uploads the canvas as a derived PNG and opens an Image editor tab on it', async () => {
		const opened = await openCanvasInImageEditor(canvasOf(new Blob(['x'])), 'Sketch.jpg');

		expect(opened).toBe(true);
		const [file, purpose] = uploadMedia.mock.calls[0];
		expect(file.name).toBe('Sketch.png');
		expect(file.type).toBe('image/png');
		expect(purpose).toBe('derived_artifact');
		expect(toolTabs()[0]).toMatchObject({
			tool: 'image',
			activeTool: 'paint',
			source: { relative_path: 'uploads/u.png', url: '/api/media/uploads/u.png', name: 'Sketch.png', type: 'image' }
		});
		expect(get(tabsStore).activeTabId).toBe(toolTabs()[0].id);
	});

	it('says so and opens nothing when the canvas cannot be encoded or the upload fails', async () => {
		expect(await openCanvasInImageEditor(canvasOf(null), 'x')).toBe(false);

		uploadMedia.mockResolvedValue({ success: false });
		expect(await openCanvasInImageEditor(canvasOf(new Blob(['x'])), 'x')).toBe(false);

		expect(error).toHaveBeenCalledTimes(2);
		expect(toolTabs()).toHaveLength(0);
	});
});

describe('the host image editor api', () => {
	it('opens a tab for openImage when no editor is open', async () => {
		const api = createHostImageEditorApi();

		expect(api.isOpen()).toBe(false);
		expect(api.openImage(canvasOf(new Blob(['x'])), 'Plugin image')).toBe(true);
		await vi.waitFor(() => expect(toolTabs()).toHaveLength(1));

		expect(toolTabs()[0].source?.name).toBe('Plugin image.png');
	});

	it('hands openImage to the editor that is open instead of opening a tab', () => {
		const openImage = vi.fn();
		setActiveEditor({ flatten: () => ({}) as HTMLCanvasElement, addImage: vi.fn(), openImage });
		const api = createHostImageEditorApi();
		const canvas = canvasOf(new Blob(['x']));

		expect(api.openImage(canvas, 'Plugin image')).toBe(true);

		expect(openImage).toHaveBeenCalledWith(canvas, 'Plugin image');
		expect(uploadMedia).not.toHaveBeenCalled();
		expect(toolTabs()).toHaveLength(0);
	});

	it('still reports false for addImage when no editor is open', () => {
		expect(createHostImageEditorApi().addImage(canvasOf(null), 'x')).toBe(false);
	});
});
