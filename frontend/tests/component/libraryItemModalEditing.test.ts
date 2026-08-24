// @vitest-environment jsdom
//
// A user browsing the Library can edit a resource there, with the same
// replace / save-as-new choice the field offers. The pure merge is asserted in
// `libraryItemEdit.test.ts`; what only mounting can show is that the modal
// offers the right tools for the medium, opens the editor on the row it is
// showing, and folds the answer back into the store.
import { describe, it, expect, vi, beforeEach } from 'vitest';

class StubResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}
vi.stubGlobal('ResizeObserver', StubResizeObserver);

const editMediaItem = vi.fn();
const listUploads = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		listLibraryItems: vi.fn().mockResolvedValue({ success: false }),
		getLibraryFacets: vi.fn().mockResolvedValue({ success: false }),
		getTags: vi.fn().mockResolvedValue({ success: false }),
		setLibraryItemTags: vi.fn(),
		listUploads: (...args: unknown[]) => listUploads(...args),
		editMediaItem: (...args: unknown[]) => editMediaItem(...args),
		extractMediaFrame: vi.fn()
	}
}));

const { libraryStore } = await import('$lib/stores/library');
const { default: LibraryItemModal } = await import(
	'../../src/routes/library/components/LibraryItemModal.svelte'
);
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

function mount(item: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({
		component: LibraryItemModal as any,
		target,
		props: { item, onClose: vi.fn(), onDeleteRequest: vi.fn() }
	});
	return target;
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

const image = {
	id: 'row-1',
	filename: 'stored-uuid.png',
	original_filename: 'portrait.png',
	media_type: 'image',
	mime_type: 'image/png',
	url: '/api/media/uploads/stored-uuid.png',
	width: 1024,
	height: 1536,
	size: 900000,
	tags: []
};

const clip = {
	id: 'row-2',
	filename: 'stored-clip.mp4',
	original_filename: 'take-3.mp4',
	media_type: 'video',
	url: '/api/media/uploads/stored-clip.mp4',
	width: 1280,
	height: 720,
	duration_seconds: 8.4,
	fps: 24,
	tags: []
};

const track = {
	id: 'row-3',
	filename: 'stored-vo.wav',
	original_filename: 'vo.wav',
	media_type: 'audio',
	url: '/api/media/uploads/stored-vo.wav',
	duration_seconds: 32.6,
	tags: []
};

beforeEach(() => {
	document.body.innerHTML = '';
	editMediaItem.mockReset();
	listUploads.mockReset();
	listUploads.mockResolvedValue({ success: true, data: { uploads: [], total: 0, limit: 100, offset: 0 } });
});

describe('LibraryItemModal editing', () => {
	it('offers only the tools the medium has', async () => {
		const withImage = mount(image);
		await settle();
		expect(buttonByText(withImage, 'Crop')).toBeTruthy();
		expect(buttonByText(withImage, 'Trim')).toBeUndefined();
		expect(buttonByText(withImage, 'Frame')).toBeUndefined();

		document.body.innerHTML = '';
		const withClip = mount(clip);
		await settle();
		expect(buttonByText(withClip, 'Crop')).toBeUndefined();
		expect(buttonByText(withClip, 'Trim')).toBeTruthy();
		expect(buttonByText(withClip, 'Frame')).toBeTruthy();

		document.body.innerHTML = '';
		const withTrack = mount(track);
		await settle();
		expect(buttonByText(withTrack, 'Trim')).toBeTruthy();
		expect(buttonByText(withTrack, 'Frame')).toBeUndefined();
	});

	it('offers both replace and save-as-new, and edits the row it is showing', async () => {
		editMediaItem.mockResolvedValue({
			success: true,
			data: {
				item: {
					id: 'row-1',
					filename: 'edited-uuid.png',
					original_filename: 'portrait.png',
					media_type: 'image',
					url: '/api/media/uploads/edited-uuid.png',
					width: 1024,
					height: 1024
				},
				replaced: true
			}
		});

		const target = mount(image);
		await settle();

		buttonByText(target, 'Crop')!.click();
		await settle();

		// The library IS the row's home, so both saves are offered here.
		expect(buttonByText(target, 'Replace original')).toBeTruthy();
		expect(buttonByText(target, 'Save as new')).toBeTruthy();

		buttonByText(target, '1:1')!.click();
		await settle();
		buttonByText(target, 'Replace original')!.click();
		await settle();

		// The row id, never the filename the file happens to be stored under.
		expect(editMediaItem.mock.calls[0][0]).toBe('row-1');
		expect(editMediaItem.mock.calls[0][2]).toBe('replace');
		// A row it already has the id of needs no lookup.
		expect(listUploads).not.toHaveBeenCalled();
	});

	it('patches the open preview onto the replaced file, keeping the row and its tags', async () => {
		const tagged = { ...image, tags: [{ id: 'tag-1', name: 'keepers' }] };
		libraryStore.setSelectedItem(tagged as never);

		await libraryStore.applyEditResult(
			{
				id: 'row-1',
				filename: 'edited-uuid.png',
				media_type: 'image',
				url: '/api/media/uploads/edited-uuid.png',
				width: 1024,
				height: 1024
			} as never,
			true
		);

		let state: any;
		libraryStore.subscribe((value) => (state = value))();
		expect(state.selectedItem.url).toBe('/api/media/uploads/edited-uuid.png');
		expect(state.selectedItem.id).toBe('row-1');
		expect(state.selectedItem.tags).toEqual([{ id: 'tag-1', name: 'keepers' }]);
	});
});
