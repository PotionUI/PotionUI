// @vitest-environment jsdom
//
// The field end of the editor seam. The pure modules prove the geometry and
// `mediaLoaderEdited` proves the value shape; what neither can prove is that
// pressing a tool in the toolbar reaches an editor, that the editor reaches the
// API, and that what lands back in `onChange` is the SERVER's url rather than a
// blob handle - which dies with the document and 404s after a refresh.
import { describe, it, expect, vi, beforeEach } from 'vitest';

class StubResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}
vi.stubGlobal('ResizeObserver', StubResizeObserver);

const editMediaItem = vi.fn();
const listUploads = vi.fn();
const listGenerationMedia = vi.fn();
const copyGenerationFileToLibrary = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		listGenerationMedia: (...args: unknown[]) => listGenerationMedia(...args),
		copyGenerationFileToLibrary: (...args: unknown[]) => copyGenerationFileToLibrary(...args),
		getUploadInfo: vi.fn().mockResolvedValue({ success: false }),
		listUploads: (...args: unknown[]) => listUploads(...args),
		editMediaItem: (...args: unknown[]) => editMediaItem(...args),
		extractMediaFrame: vi.fn(),
		uploadMedia: vi.fn()
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
	createClassComponent({ component: MediaLoaderField as any, target, props });
	return target;
}

function buttonByTitle(target: HTMLElement, title: string): HTMLButtonElement | undefined {
	return Array.from(target.querySelectorAll('button')).find(
		(button) => button.getAttribute('title') === title
	);
}

function buttonByText(target: HTMLElement, text: string): HTMLButtonElement | undefined {
	return Array.from(target.querySelectorAll('button')).find(
		(button) => (button.textContent || '').trim() === text
	);
}

async function settle() {
	for (let i = 0; i < 6; i += 1) {
		await tick();
		await Promise.resolve();
	}
}

const clip = {
	path: 'uploads/wan22_i2v_00042.mp4',
	relative_path: 'uploads/wan22_i2v_00042.mp4',
	url: '/api/media/uploads/wan22_i2v_00042.mp4',
	name: 'wan22_i2v_00042.mp4',
	type: 'video',
	metadata: { width: 1280, height: 720, duration_seconds: 5.2, fps: 24, size: 18874368 }
};

beforeEach(() => {
	document.body.innerHTML = '';
	editMediaItem.mockReset();
	listUploads.mockReset();
	listGenerationMedia.mockReset();
	copyGenerationFileToLibrary.mockReset();
	listGenerationMedia.mockResolvedValue({ success: false });
	copyGenerationFileToLibrary.mockResolvedValue({ success: false });
	listUploads.mockResolvedValue({
		success: true,
		data: {
			uploads: [{ id: 'row-42', filename: 'wan22_i2v_00042.mp4' }],
			total: 1,
			limit: 100,
			offset: 0
		}
	});
	editMediaItem.mockResolvedValue({
		success: true,
		data: {
			item: {
				id: 'row-42',
				filename: 'trimmed-9c.mp4',
				original_filename: 'wan22_i2v_00042.mp4',
				media_type: 'video',
				mime_type: 'video/mp4',
				url: '/api/media/uploads/trimmed-9c.mp4',
				width: 1280,
				height: 720,
				duration_seconds: 5.1,
				fps: 24,
				size: 18000000
			},
			replaced: true
		}
	});
});

describe('MediaLoaderField × the shared editors', () => {
	it('writes the edited resource back as the server url, never a blob handle', async () => {
		const onChange = vi.fn();
		const target = mount({
			name: 'clip',
			config: { title: 'Source video', accept: 'video/*' },
			value: clip,
			onChange
		});
		await settle();

		buttonByTitle(target, 'Trim in / out')!.click();
		await settle();

		const outHandle = Array.from(target.querySelectorAll('button')).find(
			(button) => button.getAttribute('aria-label') === 'Trim out point'
		);
		outHandle!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
		await settle();

		buttonByText(target, 'Replace original')!.click();
		await settle();

		expect(editMediaItem).toHaveBeenCalledWith(
			'row-42',
			[{ type: 'trim', start_seconds: 0, end_seconds: 5.1 }],
			'replace'
		);

		const written = onChange.mock.calls.at(-1)?.[1];
		expect(written).toMatchObject({
			relative_path: 'uploads/trimmed-9c.mp4',
			path: 'uploads/trimmed-9c.mp4',
			url: '/api/media/uploads/trimmed-9c.mp4',
			type: 'video'
		});
		// A replace lands on a NEW filename; a value left on the old one would
		// serve the pre-edit bytes out of the browser cache.
		expect(written.url).not.toContain('wan22_i2v_00042');
		expect(String(written.url).startsWith('blob:')).toBe(false);
	});

	it('closes the editor once the edit lands', async () => {
		const target = mount({
			name: 'clip',
			config: { title: 'Source video', accept: 'video/*' },
			value: clip,
			onChange: vi.fn()
		});
		await settle();

		buttonByTitle(target, 'Trim in / out')!.click();
		await settle();
		expect(buttonByText(target, 'Cancel')).toBeTruthy();

		const outHandle = Array.from(target.querySelectorAll('button')).find(
			(button) => button.getAttribute('aria-label') === 'Trim out point'
		);
		outHandle!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
		await settle();
		buttonByText(target, 'Save as new')!.click();
		await settle();

		expect(buttonByText(target, 'Cancel')).toBeUndefined();
	});

	it('offers the replace / save-as-new choice on a crop, same as the Library page', async () => {
		listUploads.mockResolvedValue({
			success: true,
			data: {
				uploads: [{ id: 'row-8', filename: 'portrait.png' }],
				total: 1,
				limit: 100,
				offset: 0
			}
		});
		const target = mount({
			name: 'reference_image',
			config: { title: 'Reference', accept: 'image/*' },
			value: {
				path: 'uploads/portrait.png',
				relative_path: 'uploads/portrait.png',
				url: '/api/media/uploads/portrait.png',
				name: 'portrait.png',
				type: 'image',
				metadata: { width: 1024, height: 1536, size: 2411724 }
			},
			onChange: vi.fn()
		});
		await settle();

		buttonByTitle(target, 'Crop & frame')!.click();
		await settle();

		// The same media must not behave differently depending on which screen
		// it was opened from.
		expect(buttonByText(target, 'Replace original')).toBeTruthy();
		expect(buttonByText(target, 'Save as new')).toBeTruthy();

		buttonByText(target, '1:1')!.click();
		await settle();
		buttonByText(target, 'Replace original')!.click();
		await settle();

		expect(editMediaItem).toHaveBeenCalledWith(
			'row-8',
			[{ type: 'crop', x: 0, y: 256, width: 1024, height: 1024 }],
			'replace'
		);
	});

	it('copies a generated image into the library before editing it', async () => {
		// The field's value may point at generated media, which is not a
		// resource at all - and every edit is server-side.
		listGenerationMedia.mockResolvedValue({
			success: true,
			data: { media: [{ id: 'file-3', filename: '0.png' }] }
		});
		copyGenerationFileToLibrary.mockResolvedValue({
			success: true,
			data: { item: { id: 'copied-1', filename: 'copy.png' } }
		});
		editMediaItem.mockResolvedValue({
			success: true,
			data: {
				item: {
					id: 'copied-1',
					filename: 'cropped.png',
					media_type: 'image',
					url: '/api/media/uploads/cropped.png'
				},
				replaced: false
			}
		});

		const onChange = vi.fn();
		const target = mount({
			name: 'reference_image',
			config: { title: 'Reference', accept: 'image/*' },
			value: {
				path: 'outputs/2026-08-13/01KABC/0.png',
				relative_path: 'outputs/2026-08-13/01KABC/0.png',
				url: '/api/media/generations/01KABC/0.png',
				name: '0.png',
				type: 'image',
				metadata: { width: 1024, height: 1536 }
			},
			onChange
		});
		await settle();

		buttonByTitle(target, 'Crop & frame')!.click();
		await settle();

		expect(copyGenerationFileToLibrary).toHaveBeenCalledWith('file-3');
		expect(target.textContent).toContain('a copy was added to your library');

		buttonByText(target, '1:1')!.click();
		await settle();
		buttonByText(target, 'Save as new')!.click();
		await settle();

		expect(editMediaItem.mock.calls[0][0]).toBe('copied-1');
		expect(onChange.mock.calls.at(-1)?.[1]).toMatchObject({
			relative_path: 'uploads/cropped.png',
			url: '/api/media/uploads/cropped.png'
		});
	});
});
