// @vitest-environment jsdom
//
// Covers the hop the pure modules cannot: that the editors actually send the
// operations the geometry produced, to the right endpoint, in the mode the
// button the user pressed means. `toCropOperation` staying green proves nothing
// if the footer wires "Replace original" to the same call as "Save as new".
import { describe, it, expect, vi, beforeEach } from 'vitest';

// The crop stage measures itself with `bind:clientWidth`, which Svelte 5
// implements with a ResizeObserver jsdom does not provide.
class StubResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}
vi.stubGlobal('ResizeObserver', StubResizeObserver);

const editMediaItem = vi.fn();
const extractMediaFrame = vi.fn();
const splitMediaItem = vi.fn();
const listUploads = vi.fn();
const listGenerationMedia = vi.fn();
const copyGenerationFileToLibrary = vi.fn();
const uploadMedia = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		editMediaItem: (...args: unknown[]) => editMediaItem(...args),
		extractMediaFrame: (...args: unknown[]) => extractMediaFrame(...args),
		splitMediaItem: (...args: unknown[]) => splitMediaItem(...args),
		listUploads: (...args: unknown[]) => listUploads(...args),
		listGenerationMedia: (...args: unknown[]) => listGenerationMedia(...args),
		copyGenerationFileToLibrary: (...args: unknown[]) => copyGenerationFileToLibrary(...args),
		uploadMedia: (...args: unknown[]) => uploadMedia(...args)
	}
}));

const { default: MediaEditors } = await import('$lib/media/editors/MediaEditors.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({ component: MediaEditors as any, target, props });
	return target;
}

function buttonByText(target: HTMLElement, text: string): HTMLButtonElement | undefined {
	return Array.from(target.querySelectorAll('button')).find(
		(button) => (button.textContent || '').trim() === text
	);
}

async function settle() {
	for (let i = 0; i < 5; i += 1) {
		await tick();
		await Promise.resolve();
	}
}

const imageSource = {
	url: '/api/media/uploads/portrait.png',
	kind: 'image' as const,
	fileName: 'portrait.png',
	itemId: 'row-1',
	storedPath: 'uploads/portrait.png',
	width: 1024,
	height: 1536
};

const clipSource = {
	url: '/api/media/uploads/clip.mp4',
	kind: 'video' as const,
	fileName: 'clip.mp4',
	itemId: 'row-2',
	storedPath: 'uploads/clip.mp4',
	width: 1280,
	height: 720,
	durationSeconds: 8.4,
	fps: 24
};

const audioSource = {
	url: '/api/media/uploads/track.mp3',
	kind: 'audio' as const,
	fileName: 'track.mp3',
	itemId: 'row-3',
	storedPath: 'uploads/track.mp3',
	durationSeconds: 65
};

beforeEach(() => {
	document.body.innerHTML = '';
	editMediaItem.mockReset();
	extractMediaFrame.mockReset();
	splitMediaItem.mockReset();
	listUploads.mockReset();
	listGenerationMedia.mockReset();
	copyGenerationFileToLibrary.mockReset();
	uploadMedia.mockReset();
	listGenerationMedia.mockResolvedValue({ success: false });
	copyGenerationFileToLibrary.mockResolvedValue({ success: false });
	uploadMedia.mockResolvedValue({
		success: true,
		data: { path: 'uploads/mask-1.png', relative_path: 'uploads/mask-1.png', filename: 'mask-1.png', size: 2, url: '/api/media/uploads/mask-1.png' }
	});
	editMediaItem.mockResolvedValue({
		success: true,
		data: {
			item: {
				id: 'row-1',
				filename: 'edited.png',
				media_type: 'image',
				url: '/api/media/uploads/edited.png'
			},
			replaced: false
		}
	});
	extractMediaFrame.mockResolvedValue({
		success: true,
		data: {
			item: {
				id: 'row-9',
				filename: 'still.png',
				media_type: 'image',
				url: '/api/media/uploads/still.png'
			},
			replaced: false
		}
	});
	splitMediaItem.mockResolvedValue({
		success: true,
		data: {
			items: [
				{ id: 'row-10', filename: 'track-part-1.mp3', media_type: 'audio', url: '/api/media/uploads/track-part-1.mp3' },
				{ id: 'row-11', filename: 'track-part-2.mp3', media_type: 'audio', url: '/api/media/uploads/track-part-2.mp3' }
			]
		}
	});
	listUploads.mockResolvedValue({ success: true, data: { uploads: [], total: 0, limit: 100, offset: 0 } });
});

describe('crop editor', () => {
	it('sends the pixel rectangle the chosen aspect produced, in the source size', async () => {
		const target = mount({
			request: { kind: 'crop', source: imageSource, itemIndex: null },
			onClose: vi.fn(),
			onResult: vi.fn()
		});
		await settle();

		// 1:1 on a 1024×1536 source is the widest square that fits, centred.
		buttonByText(target, '1:1')!.click();
		await settle();

		buttonByText(target, 'Save as new')!.click();
		await settle();

		expect(editMediaItem).toHaveBeenCalledWith(
			'row-1',
			[{ type: 'crop', x: 0, y: 256, width: 1024, height: 1024 }],
			'new'
		);
	});

	it('wires Replace original to the replace mode, not to the same call', async () => {
		const target = mount({
			request: { kind: 'crop', source: imageSource, itemIndex: null },
			onClose: vi.fn(),
			onResult: vi.fn()
		});
		await settle();

		buttonByText(target, '1:1')!.click();
		await settle();
		buttonByText(target, 'Replace original')!.click();
		await settle();

		expect(editMediaItem.mock.calls[0][2]).toBe('replace');
	});

	it('refuses to send an untouched plan, which the server rejects as empty', async () => {
		const target = mount({
			request: { kind: 'crop', source: imageSource, itemIndex: null },
			onClose: vi.fn(),
			onResult: vi.fn()
		});
		await settle();

		expect(target.textContent).toContain('Nothing to apply yet.');
		expect(buttonByText(target, 'Save as new')!.disabled).toBe(true);
		expect(editMediaItem).not.toHaveBeenCalled();
	});

	it('hands the result back and closes', async () => {
		const onResult = vi.fn();
		const onClose = vi.fn();
		const target = mount({
			request: { kind: 'crop', source: imageSource, itemIndex: 3 },
			onClose,
			onResult
		});
		await settle();

		buttonByText(target, '1:1')!.click();
		await settle();
		buttonByText(target, 'Save as new')!.click();
		await settle();

		expect(onResult).toHaveBeenCalledWith(
			{ type: 'item', item: expect.objectContaining({ filename: 'edited.png' }), replaced: false },
			expect.objectContaining({ itemIndex: 3 })
		);
		expect(onClose).toHaveBeenCalled();
	});

	it('surfaces the server refusal instead of "status code 400"', async () => {
		editMediaItem.mockRejectedValue({
			message: 'Request failed with status code 400',
			response: { data: { detail: { message: 'The crop rectangle does not fit inside 1024x1536' } } }
		});
		const onClose = vi.fn();
		const target = mount({
			request: { kind: 'crop', source: imageSource, itemIndex: null },
			onClose,
			onResult: vi.fn()
		});
		await settle();

		buttonByText(target, '1:1')!.click();
		await settle();
		buttonByText(target, 'Save as new')!.click();
		await settle();

		expect(target.textContent).toContain('The crop rectangle does not fit inside 1024x1536');
		expect(target.textContent).not.toContain('status code');
		expect(onClose).not.toHaveBeenCalled();
	});
});

describe('trim editor', () => {
	function nudgeOut(target: HTMLElement) {
		const handle = Array.from(target.querySelectorAll('button')).find(
			(button) => button.getAttribute('aria-label') === 'Trim out point'
		);
		handle!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
	}

	it('sends one trim operation, clamped to the clip', async () => {
		const target = mount({
			request: { kind: 'trim', source: clipSource, itemIndex: null },
			onClose: vi.fn(),
			onResult: vi.fn()
		});
		await settle();

		nudgeOut(target);
		await settle();
		buttonByText(target, 'Save as new')!.click();
		await settle();

		expect(editMediaItem).toHaveBeenCalledTimes(1);
		const [itemId, operations, mode] = editMediaItem.mock.calls[0];
		expect(itemId).toBe('row-2');
		expect(mode).toBe('new');
		expect(operations).toEqual([{ type: 'trim', start_seconds: 0, end_seconds: 8.3 }]);
	});

	it('refuses a selection that is still the whole clip', async () => {
		const target = mount({
			request: { kind: 'trim', source: clipSource, itemIndex: null },
			onClose: vi.fn(),
			onResult: vi.fn()
		});
		await settle();

		expect(target.textContent).toContain('Move a handle to choose what to keep.');
		expect(buttonByText(target, 'Save as new')!.disabled).toBe(true);
	});

	it('copies a generated clip into the library first, and says so', async () => {
		// A generated file is not a resource, and every edit is server-side - so
		// one is made rather than refusing to edit generated media at all.
		listGenerationMedia.mockResolvedValue({
			success: true,
			data: { media: [{ id: 'file-7', filename: '0.mp4' }] }
		});
		copyGenerationFileToLibrary.mockResolvedValue({
			success: true,
			data: { item: { id: 'copied-row', filename: 'copy.mp4' } }
		});

		const target = mount({
			request: {
				kind: 'trim',
				source: {
					...clipSource,
					itemId: null,
					storedPath: 'outputs/2026-08-13/01KABC/0.mp4'
				},
				itemIndex: null
			},
			onClose: vi.fn(),
			onResult: vi.fn()
		});
		await settle();

		expect(listGenerationMedia).toHaveBeenCalledWith('01KABC');
		expect(copyGenerationFileToLibrary).toHaveBeenCalledWith('file-7');
		// Adding to the library on the user's behalf is announced, not silent.
		expect(target.textContent).toContain('a copy was added to your library');

		nudgeOut(target);
		await settle();
		buttonByText(target, 'Save as new')!.click();
		await settle();

		expect(editMediaItem.mock.calls[0][0]).toBe('copied-row');
	});

	it('explains itself rather than offering a save when nothing can be prepared', async () => {
		listGenerationMedia.mockResolvedValue({ success: true, data: { media: [] } });

		const target = mount({
			request: {
				kind: 'trim',
				source: {
					...clipSource,
					itemId: null,
					storedPath: 'outputs/2026-08-13/01KABC/0.mp4'
				},
				itemIndex: null
			},
			onClose: vi.fn(),
			onResult: vi.fn()
		});
		await settle();

		expect(target.textContent).toContain('not in your library');
		expect(buttonByText(target, 'Save as new')!.disabled).toBe(true);
		expect(editMediaItem).not.toHaveBeenCalled();
	});

	it('resolves the library row from the stored path when the host did not supply one', async () => {
		listUploads.mockResolvedValue({
			success: true,
			data: {
				uploads: [
					{ id: 'other', filename: 'not-it.mp4' },
					{ id: 'row-77', filename: 'clip.mp4' }
				],
				total: 2,
				limit: 100,
				offset: 0
			}
		});
		const target = mount({
			request: { kind: 'trim', source: { ...clipSource, itemId: null }, itemIndex: null },
			onClose: vi.fn(),
			onResult: vi.fn()
		});
		await settle();

		nudgeOut(target);
		await settle();
		buttonByText(target, 'Save as new')!.click();
		await settle();

		expect(listUploads).toHaveBeenCalledWith(expect.objectContaining({ mediaType: 'video' }));
		expect(editMediaItem.mock.calls[0][0]).toBe('row-77');
	});
});

describe('split editor', () => {
	function partLengthInput(target: HTMLElement): HTMLInputElement {
		return target.querySelector('#split-part-seconds') as HTMLInputElement;
	}

	function setPartLength(target: HTMLElement, value: string) {
		const input = partLengthInput(target);
		input.value = value;
		input.dispatchEvent(new Event('input', { bubbles: true }));
	}

	it('sends the part length in seconds to the split endpoint, never the edit endpoint', async () => {
		const target = mount({
			request: { kind: 'split', source: audioSource, itemIndex: null },
			onClose: vi.fn(),
			onResult: vi.fn()
		});
		await settle();

		// The default part length is 10s on a 65s clip - a valid split.
		buttonByText(target, 'Split')!.click();
		await settle();

		expect(splitMediaItem).toHaveBeenCalledWith('row-3', 10);
		expect(editMediaItem).not.toHaveBeenCalled();
	});

	it('previews the part count, remainder clip included, recomputed as the user types', async () => {
		const target = mount({
			request: { kind: 'split', source: audioSource, itemIndex: null },
			onClose: vi.fn(),
			onResult: vi.fn()
		});
		await settle();

		// 65s at 10s a part is 6 full parts plus a 5s remainder - 7 parts, not 6.
		expect(target.textContent).toContain('7 parts (6 × 10s + 5s)');

		setPartLength(target, '13');
		await settle();

		// 65 / 13 divides evenly - no remainder clip, and no "+ 0s" in the text.
		expect(target.textContent).toContain('5 parts (5 × 13s)');
		expect(target.textContent).not.toContain('+ 0s');
	});

	it('disables the action once the part length reaches the clip length', async () => {
		const target = mount({
			request: { kind: 'split', source: audioSource, itemIndex: null },
			onClose: vi.fn(),
			onResult: vi.fn()
		});
		await settle();

		setPartLength(target, '65');
		await settle();

		expect(buttonByText(target, 'Split')!.disabled).toBe(true);
		expect(target.textContent).toContain('nothing to split');

		buttonByText(target, 'Split')!.click();
		await settle();
		expect(splitMediaItem).not.toHaveBeenCalled();
	});

	it('hands back every part the split produced, not just one', async () => {
		const onResult = vi.fn();
		const onClose = vi.fn();
		const target = mount({
			request: { kind: 'split', source: audioSource, itemIndex: null },
			onClose,
			onResult
		});
		await settle();

		buttonByText(target, 'Split')!.click();
		await settle();

		expect(onResult).toHaveBeenCalledWith(
			{
				type: 'items',
				items: [
					expect.objectContaining({ filename: 'track-part-1.mp3' }),
					expect.objectContaining({ filename: 'track-part-2.mp3' })
				]
			},
			expect.objectContaining({ itemIndex: null })
		);
		expect(onClose).toHaveBeenCalled();
	});
});

describe('mask editor', () => {
	it('needs no library resource, so opening one looks nothing up', async () => {
		// A mask is painted over the media and stored beside it; it changes no
		// row, so it must not pay for - or be blocked by - a resource lookup.
		mount({
			request: { kind: 'mask', source: { ...imageSource, itemId: null }, itemIndex: null },
			onClose: vi.fn(),
			onResult: vi.fn()
		});
		await settle();

		expect(listUploads).not.toHaveBeenCalled();
		expect(listGenerationMedia).not.toHaveBeenCalled();
		expect(copyGenerationFileToLibrary).not.toHaveBeenCalled();
	});
});

describe('frame editor', () => {
	it('posts to the frame endpoint and never offers a replace', async () => {
		const target = mount({
			request: { kind: 'frame', source: clipSource, itemIndex: null },
			onClose: vi.fn(),
			onResult: vi.fn()
		});
		await settle();

		// A still cannot take a clip's place - every reference to the row was
		// given a video - so the choice collapses to one button.
		expect(buttonByText(target, 'Replace original')).toBeUndefined();

		buttonByText(target, 'Save frame')!.click();
		await settle();

		expect(extractMediaFrame).toHaveBeenCalledWith('row-2', 0);
		expect(editMediaItem).not.toHaveBeenCalled();
	});

	it('never asks for the duration itself, which the server treats as past the end', async () => {
		const target = mount({
			request: { kind: 'frame', source: clipSource, itemIndex: null },
			onClose: vi.fn(),
			onResult: vi.fn()
		});
		await settle();

		const rail = target.querySelector('[role="presentation"]') as HTMLElement;
		rail.dispatchEvent(
			new PointerEvent('pointerdown', { button: 0, clientX: 99999, bubbles: true })
		);
		await settle();
		buttonByText(target, 'Save frame')!.click();
		await settle();

		const [, requestedTime] = extractMediaFrame.mock.calls[0];
		expect(requestedTime).toBeLessThan(8.4);
	});
});
