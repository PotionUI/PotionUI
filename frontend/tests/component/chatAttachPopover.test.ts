// @vitest-environment jsdom
//
// ChatAttachPopover embeds the real MediaLoaderField, so it needs the same API
// mocks that field's own component tests use (mediaLoaderFieldFaces.test.ts) —
// otherwise mounting throws on unmocked network calls. Covers the two
// self-contained actions this component owns: picking a "from current form"
// thumbnail and reusing the last generated image, both of which must call
// their prop callback AND close the popover (`onClose`) in one click.
import { describe, it, expect, vi } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		listGenerationMedia: vi.fn().mockResolvedValue({ success: false }),
		getUploadInfo: vi.fn().mockResolvedValue({ success: false }),
		listUploads: vi
			.fn()
			.mockResolvedValue({ success: true, data: { uploads: [], total: 0, limit: 100, offset: 0 } }),
		editMediaItem: vi.fn(),
		extractMediaFrame: vi.fn(),
		listLibraryItems: vi.fn().mockResolvedValue({ success: true, data: { items: [], total: 0 } }),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } })
	}
}));

vi.mock('$lib/utils/storage', () => ({
	storage: { get: vi.fn().mockReturnValue(null), set: vi.fn(), remove: vi.fn() }
}));

const { default: ChatAttachPopover } = await import(
	'$lib/components/chat/ChatAttachPopover.svelte'
);
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

function formImage(key: string, name: string) {
	return {
		key,
		label: name,
		media: { path: `uploads/${key}.png`, relative_path: `uploads/${key}.png`, name },
		url: `/api/media/uploads/${key}.png`
	};
}

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: ChatAttachPopover as never, target, props });
	return { target, component };
}

describe('ChatAttachPopover', () => {
	it('picking a from-current-form thumbnail selects it and closes the popover', () => {
		const onSelectFormImage = vi.fn();
		const onClose = vi.fn();
		const entries = [formImage('a', 'a.png'), formImage('b', 'b.png')];
		const { component } = mount({
			onClose,
			formImageEntries: entries,
			selectedDurablePath: null,
			onSelectFormImage
		});

		const thumb = document.body.querySelector('button[aria-label="a.png"]') as HTMLButtonElement;
		expect(thumb).toBeTruthy();
		thumb.click();

		expect(onSelectFormImage).toHaveBeenCalledWith(entries[0]);
		expect(onClose).toHaveBeenCalledTimes(1);

		component.$destroy?.();
	});

	it('marks the entry matching selectedDurablePath as selected', () => {
		const entries = [formImage('a', 'a.png'), formImage('b', 'b.png')];
		const { component } = mount({
			onClose: vi.fn(),
			formImageEntries: entries,
			selectedDurablePath: 'uploads/b.png',
			onSelectFormImage: vi.fn()
		});

		const selectedThumb = document.body.querySelector('button[aria-label="b.png"]') as HTMLButtonElement;
		expect(selectedThumb.className).toContain('selected');
		const unselectedThumb = document.body.querySelector('button[aria-label="a.png"]') as HTMLButtonElement;
		expect(unselectedThumb.className).not.toContain('selected');

		component.$destroy?.();
	});

	it('"Attach again" reuses the last generated image and closes the popover', () => {
		const onAttachLastImage = vi.fn();
		const onClose = vi.fn();
		const { component } = mount({
			onClose,
			onAttachLastImage,
			lastGeneratedImage: { url: '/api/media/uploads/last.png', name: 'last.png', generatedAt: '2 min ago' }
		});

		const reuseButton = Array.from(document.body.querySelectorAll('button')).find(
			(b) => b.textContent?.trim() === 'Attach again'
		) as HTMLButtonElement;
		expect(reuseButton).toBeTruthy();
		reuseButton.click();

		expect(onAttachLastImage).toHaveBeenCalledTimes(1);
		expect(onClose).toHaveBeenCalledTimes(1);

		component.$destroy?.();
	});

	it('the auto-attach checkbox reflects alwaysAttachLastImage and toggles via onToggleAttachImage', () => {
		const onToggleAttachImage = vi.fn();
		const { component } = mount({
			onClose: vi.fn(),
			alwaysAttachLastImage: true,
			onToggleAttachImage
		});

		const checkbox = document.body.querySelector('input[type="checkbox"]') as HTMLInputElement;
		expect(checkbox.checked).toBe(true);
		checkbox.dispatchEvent(new Event('change'));

		expect(onToggleAttachImage).toHaveBeenCalledTimes(1);

		component.$destroy?.();
	});

	it('keeps the popover open when a press lands in a menu the embedded field portals out', () => {
		const onClose = vi.fn();
		const { component } = mount({ onClose, formImageEntries: [], selectedDurablePath: null, onSelectFormImage: vi.fn() });
		const menu = document.createElement('div');
		menu.setAttribute('data-source-menu', '');
		const item = document.createElement('button');
		menu.appendChild(item);
		document.body.appendChild(menu);

		item.dispatchEvent(new Event('pointerdown', { bubbles: true }));
		expect(onClose).not.toHaveBeenCalled();

		const outside = document.createElement('div');
		document.body.appendChild(outside);
		outside.dispatchEvent(new Event('pointerdown', { bubbles: true }));
		expect(onClose).toHaveBeenCalledTimes(1);

		menu.remove();
		outside.remove();
		component.$destroy?.();
	});
	it('keeps the popover open on Escape while a field menu is open, and closes on Escape after it is gone', () => {
		const onClose = vi.fn();
		const { component } = mount({ onClose, formImageEntries: [], selectedDurablePath: null, onSelectFormImage: vi.fn() });
		const menu = document.createElement('div');
		menu.setAttribute('data-source-menu', '');
		document.body.appendChild(menu);

		document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		expect(onClose).not.toHaveBeenCalled();

		menu.remove();
		document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		expect(onClose).toHaveBeenCalledTimes(1);

		component.$destroy?.();
	});

	it('shows the regular media field as one dropzone with Browse, History and Library', async () => {
		const { component } = mount({ onClose: vi.fn() });
		await tick();

		expect(document.body.querySelectorAll('[data-media-field]')).toHaveLength(1);
		expect(document.body.querySelectorAll('[data-media-dropzone]')).toHaveLength(1);
		expect(document.body.querySelector('.media-loader-field')).toBeNull();
		const doors = Array.from(document.body.querySelectorAll('[data-media-dropzone] button')).map(
			(b) => b.textContent?.trim()
		);
		for (const door of ['Browse', 'History', 'Library']) expect(doors).toContain(door);

		component.$destroy?.();
	});

	it('shows a single thumbnail with replace and remove once an image is picked', async () => {
		const { component } = mount({ onClose: vi.fn() });
		component.$set({
			selectedImageData: {
				path: 'uploads/p.png',
				relative_path: 'uploads/p.png',
				url: '/api/media/uploads/p.png',
				name: 'p.png',
				type: 'image',
				metadata: { width: 512, height: 512, size: 1000 }
			}
		});
		await tick();
		await tick();

		expect(document.body.querySelectorAll('[data-media-inspector]')).toHaveLength(1);
		expect(document.body.querySelector('[data-media-dropzone]')).toBeNull();
		const inspector = document.body.querySelector('[data-media-inspector]')!;
		expect(inspector.querySelectorAll('img')).toHaveLength(1);
		const labels = Array.from(inspector.querySelectorAll('button')).map((b) => b.getAttribute('aria-label'));
		expect(labels).toContain('Replace');
		expect(labels).toContain('Remove');

		component.$destroy?.();
	});
});
