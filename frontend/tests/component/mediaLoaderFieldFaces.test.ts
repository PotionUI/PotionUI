// @vitest-environment jsdom
//
// Covers the hops the pure modules cannot: that the field actually renders the
// face its state selects, that a drop reaches the limit check before anything
// is uploaded, and that a reorder writes the array the arithmetic produced back
// out through `onChange`. `moveWithinLane` staying green proves nothing if the
// tile's drop handler passes it the wrong lane.
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		listGenerationMedia: vi.fn().mockResolvedValue({ success: false }),
		getUploadInfo: vi.fn().mockResolvedValue({ success: false }),
		// The field mounts the media editors, and a trim resolves the library
		// row behind the clip before it can offer a save.
		listUploads: vi
			.fn()
			.mockResolvedValue({ success: true, data: { uploads: [], total: 0, limit: 100, offset: 0 } }),
		editMediaItem: vi.fn(),
		extractMediaFrame: vi.fn(),
		// Backs the "Pick from the library" door (UploadLibraryModal), a
		// different picker from the history/upload ones above.
		listLibraryItems: vi.fn().mockResolvedValue({ success: true, data: { items: [], total: 0 } }),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } })
	}
}));

vi.mock('$lib/utils/storage', () => ({
	storage: { get: vi.fn().mockReturnValue(null), set: vi.fn(), remove: vi.fn() }
}));

const { default: MediaLoaderField } = await import(
	'$lib/components/form-fields/MediaLoaderField.svelte'
);
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: MediaLoaderField as any, target, props });
	return { target, component };
}

function imageItem(id: string, extra: Record<string, unknown> = {}) {
	return {
		path: `uploads/${id}.png`,
		relative_path: `uploads/${id}.png`,
		url: `/api/media/uploads/${id}.png`,
		name: `${id}.png`,
		type: 'image',
		metadata: { width: 1024, height: 1024, size: 2411724 },
		...extra
	};
}

function videoItem(id: string) {
	return {
		path: `uploads/${id}.mp4`,
		relative_path: `uploads/${id}.mp4`,
		url: `/api/media/uploads/${id}.mp4`,
		name: `${id}.mp4`,
		type: 'video',
		metadata: { width: 1280, height: 720, duration_seconds: 5.2, fps: 24, size: 18874368 }
	};
}

function audioItem(id: string) {
	return {
		path: `uploads/${id}.mp3`,
		relative_path: `uploads/${id}.mp3`,
		url: `/api/media/uploads/${id}.mp3`,
		name: `${id}.mp3`,
		type: 'audio',
		metadata: { duration_seconds: 12.4, size: 512000 }
	};
}

function tiles(target: HTMLElement): HTMLElement[] {
	return Array.from(target.querySelectorAll<HTMLElement>('[data-media-tile]'));
}

// Looked up in JS rather than with an attribute selector: jsdom's selector
// engine does not match a `&` inside one, and half these titles contain one.
function buttonTitles(target: HTMLElement): (string | null)[] {
	return Array.from(target.querySelectorAll('button')).map((b) => b.getAttribute('title'));
}

function buttonByTitle(target: HTMLElement, title: string): HTMLButtonElement | undefined {
	return Array.from(target.querySelectorAll('button')).find((b) => b.getAttribute('title') === title);
}

function fireDrag(element: HTMLElement, type: string, dataTransfer: Record<string, unknown>) {
	const event = new Event(type, { bubbles: true, cancelable: true });
	Object.defineProperty(event, 'dataTransfer', { value: dataTransfer });
	element.dispatchEvent(event);
	return event;
}

beforeEach(() => {
	document.body.innerHTML = '';
});

describe('empty face', () => {
	it('names what it takes and lists every door', () => {
		const { target } = mount({
			name: 'reference_image',
			config: { title: 'Reference image', accept: 'image/*' },
			value: null,
			onChange: vi.fn()
		});

		expect(target.textContent).toContain('Drop an image here');
		expect(target.textContent).toContain('PNG · JPG · WEBP');
		expect(buttonTitles(target)).toEqual(
			expect.arrayContaining([
				'Browse files',
				'Paste from clipboard',
				'Pick from generation history',
				'Pick from the library'
			])
		);
	});

	// History's `mediaType` filter (and the list API behind it) is image/video
	// only, so an audio field has no filtered history to offer.
	it('offers no history door on an audio-only field', () => {
		const { target } = mount({
			name: 'voice',
			config: { title: 'Voice track', accept: 'audio/*' },
			value: null,
			onChange: vi.fn()
		});

		expect(buttonTitles(target)).not.toContain('Pick from generation history');
		expect(target.textContent).toContain('Drop an audio file here');
	});
});

describe('loaded face', () => {
	it('puts the tools in a toolbar under the preview, not over the media', async () => {
		const { target } = mount({
			name: 'reference_image',
			config: { title: 'Reference image', accept: 'image/*' },
			value: imageItem('sdxl_portrait_0043'),
			onChange: vi.fn()
		});
		await tick();

		const crop = buttonByTitle(target, 'Crop & frame');
		expect(crop).toBeTruthy();
		// The toolbar is a sibling of the media, not positioned over it.
		expect(crop!.closest('.absolute')).toBeNull();

		expect(buttonTitles(target)).toEqual(
			expect.arrayContaining(['Crop & frame', 'View full size', 'Replace media', 'Remove'])
		);
		expect(target.textContent).toContain('1024×1024');
		expect(target.textContent).toContain('2.3 MB');
	});

	it('offers trim on a video, and opens the built-in editor when no host intercepts', async () => {
		const withoutHost = mount({
			name: 'clip',
			config: { title: 'Source video', accept: 'video/*' },
			value: videoItem('wan22_i2v_00042'),
			onChange: vi.fn()
		});
		await tick();

		const builtIn = buttonByTitle(withoutHost.target, 'Trim in / out');
		expect(builtIn).toBeTruthy();
		builtIn!.click();
		await tick();
		// The field mounts the shared editors itself, so the tool is never a
		// button that does nothing.
		expect(document.body.textContent).toContain('Trim in / out');

		const onOpenEditor = vi.fn();
		const withHost = mount({
			name: 'clip',
			config: { title: 'Source video', accept: 'video/*' },
			value: videoItem('wan22_i2v_00042'),
			onChange: vi.fn(),
			onOpenEditor
		});
		await tick();

		const trim = buttonByTitle(withHost.target, 'Trim in / out');
		expect(trim).toBeTruthy();
		trim!.click();
		expect(onOpenEditor).toHaveBeenCalledWith(
			expect.objectContaining({
				kind: 'trim',
				itemIndex: null,
				source: expect.objectContaining({ kind: 'video' })
			})
		);
	});
});

describe('rejection face', () => {
	it('refuses a kind the field does not take, before uploading anything', async () => {
		const onChange = vi.fn();
		// The upload goes out over XHR (it needs progress events), so this is
		// the call that must not happen - not `fetch`.
		const sendSpy = vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(() => {});
		const { target } = mount({
			name: 'reference_image',
			config: { title: 'Reference image', accept: 'image/*' },
			value: null,
			onChange
		});

		const dropzone = target.querySelector<HTMLElement>('[role="button"]')!;
		const file = new File(['x'], 'take_04.mov', { type: 'video/quicktime' });
		fireDrag(dropzone, 'drop', { files: [file] });
		await tick();
		await tick();

		// Same clause the server would send back on submit, so a user who trips
		// the limit here and one who trips it on submit read the same sentence.
		expect(target.textContent).toContain(
			"Type 'video' is not accepted for 'reference_image' (accepted: image)"
		);
		expect(target.textContent).toContain('take_04.mov · video/quicktime');
		expect(onChange).not.toHaveBeenCalled();
		expect(sendSpy).not.toHaveBeenCalled();
		sendSpy.mockRestore();
	});
});

describe('multi face', () => {
	it('numbers the items and reorders them to the slot they were dropped on', async () => {
		const onChange = vi.fn();
		const value = [imageItem('a'), imageItem('b'), imageItem('c')];
		const { target } = mount({
			name: 'refs',
			config: { title: 'Reference images', accept: 'image/*', multiple: true, max_items: 6 },
			value,
			onChange
		});
		await tick();

		const rendered = tiles(target);
		expect(rendered).toHaveLength(3);
		expect(rendered.map((tile) => tile.querySelector('span')?.textContent?.trim())).toEqual(['1', '2', '3']);

		const dataTransfer = { setData: vi.fn(), effectAllowed: '' };
		fireDrag(rendered[0], 'dragstart', dataTransfer);
		fireDrag(rendered[2], 'dragover', dataTransfer);
		fireDrag(rendered[2], 'drop', dataTransfer);

		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange.mock.calls[0][1].map((item: { name: string }) => item.name)).toEqual([
			'b.png',
			'c.png',
			'a.png'
		]);
	});

	it('lanes a mixed field and numbers each lane from one', async () => {
		const { target } = mount({
			name: 'inputs',
			config: { title: 'Input media', accept: 'image/*,video/*', multiple: true },
			value: [imageItem('a'), videoItem('v'), imageItem('b')],
			onChange: vi.fn()
		});
		await tick();

		expect(target.textContent).toContain('Images');
		expect(target.textContent).toContain('Video');
		const badges = tiles(target).map((tile) => tile.querySelector('span')?.textContent?.trim());
		// Two image tiles numbered 1,2 then the single video tile numbered 1.
		expect(badges).toEqual(['1', '2', '1']);
	});

	it('reorders within a lane without disturbing the other lane', async () => {
		const onChange = vi.fn();
		const { target } = mount({
			name: 'inputs',
			config: { title: 'Input media', accept: 'image/*,video/*', multiple: true },
			value: [imageItem('a'), videoItem('v'), imageItem('b')],
			onChange
		});
		await tick();

		const rendered = tiles(target);
		const dataTransfer = { setData: vi.fn(), effectAllowed: '' };
		fireDrag(rendered[0], 'dragstart', dataTransfer);
		fireDrag(rendered[1], 'dragover', dataTransfer);
		fireDrag(rendered[1], 'drop', dataTransfer);

		expect(onChange.mock.calls[0][1].map((item: { name: string }) => item.name)).toEqual([
			'b.png',
			'v.mp4',
			'a.png'
		]);
	});

	it('refuses a drop from one lane onto another', async () => {
		const onChange = vi.fn();
		const { target } = mount({
			name: 'inputs',
			config: { title: 'Input media', accept: 'image/*,video/*', multiple: true },
			value: [imageItem('a'), videoItem('v'), imageItem('b')],
			onChange
		});
		await tick();

		const rendered = tiles(target);
		const dataTransfer = { setData: vi.fn(), effectAllowed: '' };
		// Tile index 2 in DOM order is the video lane's only tile.
		fireDrag(rendered[0], 'dragstart', dataTransfer);
		fireDrag(rendered[2], 'drop', dataTransfer);

		expect(onChange).not.toHaveBeenCalled();
	});

	// An editor result has to come back to the tile it left from. If the field
	// hands the editor "the current item" instead of an index, a reorder while
	// the editor is open applies the edit to the wrong reference.
	it('opens a tile editor against that tile, by index', async () => {
		const onOpenEditor = vi.fn();
		const { target } = mount({
			name: 'refs',
			config: { title: 'Reference images', accept: 'image/*', multiple: true },
			value: [imageItem('a'), imageItem('b'), imageItem('c')],
			onChange: vi.fn(),
			onOpenEditor
		});
		await tick();

		const second = tiles(target)[1];
		const crop = Array.from(second.querySelectorAll('button')).find(
			(b) => b.getAttribute('title') === 'Crop & frame'
		);
		expect(crop).toBeTruthy();
		crop!.click();

		expect(onOpenEditor).toHaveBeenCalledWith(
			expect.objectContaining({
				kind: 'crop',
				itemIndex: 1,
				source: expect.objectContaining({
					kind: 'image',
					url: '/api/media/uploads/b.png',
					storedPath: 'uploads/b.png'
				})
			})
		);
	});

	it('reports the cap and offers a way out once every slot is used', async () => {
		const onChange = vi.fn();
		const { target } = mount({
			name: 'refs',
			config: { title: 'Reference images', accept: 'image/*', multiple: true, max_items: 2 },
			value: [imageItem('a'), imageItem('b')],
			onChange
		});
		await tick();

		expect(target.textContent).toContain('All 2 slots used');
		const clearAll = Array.from(target.querySelectorAll('button')).find(
			(b) => b.textContent?.trim() === 'Clear all'
		);
		expect(clearAll).toBeTruthy();
		clearAll!.click();
		expect(onChange).toHaveBeenCalledWith('refs', []);
	});

	// Every kind gets a peek, not just image - an audio tile is a bare icon
	// and a video tile a silent, unplayable loop, so both need the full-size
	// door at least as much as the image tile does.
	it('peeks a multi tile full size, as whichever kind that tile actually is', async () => {
		const { target } = mount({
			name: 'inputs',
			config: { title: 'Input media', accept: 'image/*,video/*,audio/*', multiple: true },
			value: [imageItem('a'), videoItem('v'), audioItem('s')],
			onChange: vi.fn()
		});
		await tick();

		const rendered = tiles(target);

		function peekButton(tile: HTMLElement): HTMLButtonElement {
			const btn = Array.from(tile.querySelectorAll('button')).find(
				(b) => b.getAttribute('title') === 'View full size'
			);
			expect(btn).toBeTruthy();
			return btn!;
		}

		peekButton(rendered[0]).click();
		await tick();
		// MediaPreviewModal portals itself onto document.body
		// (src/lib/actions/portal.ts), so it never appears inside `target`.
		let dialog = document.body.querySelector('[aria-label="Media preview"]');
		expect(dialog).toBeTruthy();
		expect(dialog!.querySelector('img')?.getAttribute('src')).toBe('/api/media/uploads/a.png');

		dialog!.querySelector<HTMLButtonElement>('[aria-label="Close preview"]')!.click();
		await tick();
		expect(document.body.querySelector('[aria-label="Media preview"]')).toBeNull();

		peekButton(rendered[1]).click();
		await tick();
		dialog = document.body.querySelector('[aria-label="Media preview"]');
		expect(dialog!.querySelector('video')?.getAttribute('src')).toBe('/api/media/uploads/v.mp4');

		dialog!.querySelector<HTMLButtonElement>('[aria-label="Close preview"]')!.click();
		await tick();

		peekButton(rendered[2]).click();
		await tick();
		dialog = document.body.querySelector('[aria-label="Media preview"]');
		// Audio has no thumbnail to blow up to full size - the modal renders a
		// playback surface instead, never an <img> or <video>.
		expect(dialog!.querySelector('img')).toBeNull();
		expect(dialog!.querySelector('video')).toBeNull();
		expect(dialog!.querySelector('audio')).toBeTruthy();
	});

	// The View-full-size button is the discoverable/touch affordance; a
	// double-click on the thumbnail itself is the mouse-user shortcut to the
	// same door - but only the thumbnail, never the label row underneath
	// (double-clicking the label input is how you select a word to retype).
	it('opens the peek modal on a double-click of the tile thumbnail, never the label row', async () => {
		const { target } = mount({
			name: 'inputs',
			config: { title: 'Input media', accept: 'image/*', multiple: true },
			value: [imageItem('a')],
			onChange: vi.fn()
		});
		await tick();

		const tile = tiles(target)[0];
		const thumbnail = tile.querySelector<HTMLElement>('.aspect-square');
		const labelInput = tile.querySelector<HTMLInputElement>('input[type="text"]');
		expect(thumbnail).toBeTruthy();
		expect(labelInput).toBeTruthy();

		labelInput!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
		await tick();
		expect(document.body.querySelector('[aria-label="Media preview"]')).toBeNull();

		thumbnail!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
		await tick();
		const dialog = document.body.querySelector('[aria-label="Media preview"]');
		expect(dialog).toBeTruthy();
		expect(dialog!.querySelector('img')?.getAttribute('src')).toBe('/api/media/uploads/a.png');
	});
});

describe('library pick', () => {
	it('carries the library display name into a fresh item as its label, but never a placeholder', async () => {
		const { api } = await import('$lib/services/api/index');
		(api.listLibraryItems as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
			success: true,
			data: {
				items: [
					{
						id: 'lib1',
						filename: 'a1b2.png',
						original_filename: 'sunset_beach.png',
						media_type: 'image',
						url: '/api/media/uploads/a1b2.png'
					},
					{
						id: 'lib2',
						filename: 'c3d4.png',
						original_filename: '',
						media_type: 'image',
						url: '/api/media/uploads/c3d4.png'
					}
				],
				total: 2
			}
		});

		const onChange = vi.fn();
		const { target } = mount({
			name: 'refs',
			config: { title: 'Reference images', accept: 'image/*', multiple: true },
			value: [],
			onChange
		});
		await tick();

		buttonByTitle(target, 'Pick from the library')!.click();
		await tick();
		await tick();
		await tick();

		// UploadLibraryModal renders through a `use:portal` action straight onto
		// <body>, not as a descendant of `target` - it is still inside `target`'s
		// own document, just not under this node.
		const named = document.body.querySelector<HTMLButtonElement>('[aria-label="Use sunset_beach.png"]');
		expect(named).toBeTruthy();
		named!.click();

		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange.mock.calls[0][1][0]).toMatchObject({
			name: 'sunset_beach.png',
			label: 'sunset_beach.png'
		});

		onChange.mockClear();
		const untitled = document.body.querySelector<HTMLButtonElement>('[aria-label="Use Untitled"]');
		expect(untitled).toBeTruthy();
		untitled!.click();

		expect(onChange).toHaveBeenCalledTimes(1);
		// original_filename was empty - the item still needs A name to show
		// (falls back to "Upload"), but that filler must never masquerade as a
		// user-facing label.
		expect(onChange.mock.calls[0][1][0]).not.toHaveProperty('label');
	});
});
