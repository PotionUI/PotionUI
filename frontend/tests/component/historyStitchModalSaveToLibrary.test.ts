// @vitest-environment jsdom
//
// The Stitch modal's footer has two actions that write different places:
// "Download PNG" saves to disk, "Save to Library" uploads the same
// composition into the user's Library. Both encode through the same
// canvas -> Blob step, so what is worth proving here is the Library path's
// own wiring - the File it builds, that a successful upload closes the
// modal without touching history (`onClose`, never `onDone`), and that a
// failed upload leaves the modal open with an error toast.
//
// BaseModal portals its dialog onto <body>, so assertions read from
// `document`, not the mount target. jsdom has no canvas backend, no
// ResizeObserver and no createImageBitmap, so all three are stubbed below.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { get } from 'svelte/store';

class StubResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}
vi.stubGlobal('ResizeObserver', StubResizeObserver);

class StubCanvasContext {
	fillStyle: unknown;
	font = '';
	textBaseline = 'alphabetic';
	imageSmoothingQuality = 'low';
	clearRect() {}
	fillRect() {}
	drawImage() {}
	fillText() {}
	setTransform() {}
}
vi.stubGlobal(
	'createImageBitmap',
	vi.fn().mockResolvedValue({ width: 64, height: 64, close: vi.fn() })
);
HTMLCanvasElement.prototype.getContext = vi.fn(() => new StubCanvasContext()) as never;
HTMLCanvasElement.prototype.toBlob = function (callback: BlobCallback) {
	callback(new Blob(['stub-png'], { type: 'image/png' }));
} as never;

const getGenerationImageURL = vi.fn();
const getClient = vi.fn();
const getGenerationParams = vi.fn();
const uploadLibraryMedia = vi.fn();
const listLibraryItems = vi.fn();
const getLibraryFacets = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationImageURL: (...args: unknown[]) => getGenerationImageURL(...args),
		getClient: (...args: unknown[]) => getClient(...args),
		getGenerationParams: (...args: unknown[]) => getGenerationParams(...args),
		uploadLibraryMedia: (...args: unknown[]) => uploadLibraryMedia(...args),
		listLibraryItems: (...args: unknown[]) => listLibraryItems(...args),
		getLibraryFacets: (...args: unknown[]) => getLibraryFacets(...args)
	}
}));

const { toasts } = await import('$lib/stores/toast');
const { default: HistoryStitchModal } = await import(
	'../../src/routes/history/components/HistoryStitchModal.svelte'
);
const { stitchFileName } = await import('../../src/lib/history/stitch');
import type { MediaToolContext } from '../../src/lib/tools/tools';
import type { GenerationHistoryItem, GenerationFile } from '../../src/lib/types/history';

const GENERATION = {
	id: 'gen-1',
	form_data: {},
	segments: []
} as unknown as GenerationHistoryItem;

const FILE = {
	id: 1,
	file_path: 'a.png',
	file_type: 'image',
	is_final: true,
	width: 64,
	height: 64
} as unknown as GenerationFile;

const CONTEXT: MediaToolContext = {
	scope: 'history',
	generations: [GENERATION],
	generationIds: ['gen-1'],
	files: [{ generation: GENERATION, file: FILE, index: 0, kind: 'image' }],
	items: [
		{
			id: 'gen-1:0',
			kind: 'image',
			url: '/api/media/generations/gen-1/a.png',
			filename: 'a.png',
			width: 64,
			height: 64,
			generationId: 'gen-1',
			paramIndex: 0
		}
	],
	kinds: new Set(['image']),
	collectionId: null
};

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | undefined;
let onClose: ReturnType<typeof vi.fn>;
let onDone: ReturnType<typeof vi.fn>;

async function mountModal() {
	target = document.createElement('div');
	document.body.appendChild(target);
	onClose = vi.fn();
	onDone = vi.fn();
	component = mount(HistoryStitchModal, {
		target,
		props: { context: CONTEXT, onClose, onDone }
	});
	// One effect pass loads the image (getClient().get + getGenerationParams,
	// both microtasks), then a debounced preview render (120ms) follows -
	// neither needs to settle for the footer's Save button to be usable.
	for (let i = 0; i < 8; i++) await Promise.resolve();
	flushSync();
	await new Promise((resolve) => setTimeout(resolve, 150));
	flushSync();
}

function saveButton() {
	return Array.from(document.querySelectorAll('button')).find((b) =>
		b.textContent?.includes('Save to Library')
	);
}

beforeEach(() => {
	getGenerationImageURL.mockReturnValue('/api/generations/gen-1/files/a.png');
	getClient.mockReturnValue({
		get: vi.fn().mockResolvedValue({ data: new Blob(['img'], { type: 'image/png' }) })
	});
	getGenerationParams.mockResolvedValue({ success: false });
	listLibraryItems.mockResolvedValue({ success: true, data: { items: [], total: 0 } });
	getLibraryFacets.mockResolvedValue({ success: true, data: { media_types: {} } });
});

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	target?.remove();
	document.body.innerHTML = '';
	vi.clearAllMocks();
	get(toasts).forEach((t) => toasts.remove(t.id));
});

describe('HistoryStitchModal - Save to Library', () => {
	it('uploads the stitched PNG, toasts success and closes without marking data changed', async () => {
		uploadLibraryMedia.mockResolvedValue({ success: true, data: { filename: 'stitch.png' } });
		await mountModal();

		expect(saveButton()).toBeTruthy();
		saveButton()!.click();
		await new Promise((resolve) => setTimeout(resolve, 0));
		flushSync();
		await new Promise((resolve) => setTimeout(resolve, 0));
		flushSync();

		expect(uploadLibraryMedia).toHaveBeenCalledTimes(1);
		const uploaded = uploadLibraryMedia.mock.calls[0][0] as File;
		expect(uploaded).toBeInstanceOf(File);
		expect(uploaded.type).toBe('image/png');
		expect(uploaded.name).toMatch(/^stitch-\d{8}-\d{4}\.png$/);

		expect(get(toasts).at(-1)?.type).toBe('success');
		expect(get(toasts).at(-1)?.message).toBe('Saved to your Library');

		expect(onClose).toHaveBeenCalledTimes(1);
		expect(onDone).not.toHaveBeenCalled();
	});

	it('keeps the modal open and toasts an error when the upload fails', async () => {
		uploadLibraryMedia.mockResolvedValue({ success: false });
		await mountModal();

		saveButton()!.click();
		await new Promise((resolve) => setTimeout(resolve, 0));
		flushSync();
		await new Promise((resolve) => setTimeout(resolve, 0));
		flushSync();

		expect(get(toasts).at(-1)?.type).toBe('error');
		expect(get(toasts).at(-1)?.message).toBe('Could not save to Library');

		expect(onClose).not.toHaveBeenCalled();
		expect(onDone).not.toHaveBeenCalled();
		// The modal itself is still there to retry from.
		expect(document.body.textContent).toContain('Stitch');
	});

	it('names the upload the same way the download filename is built', () => {
		expect(stitchFileName(new Date(2026, 8, 9, 4, 7))).toBe('stitch-20260909-0407.png');
	});
});
