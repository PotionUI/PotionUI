// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		listGenerationMedia: vi.fn().mockResolvedValue({ success: false }),
		getUploadInfo: vi.fn().mockResolvedValue({ success: false }),
		getHistoryTools: vi.fn().mockResolvedValue({ success: true, data: [] }),
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

vi.mock('$lib/components/form-fields/mediaLoaderProbe', () => ({
	probeMediaFile: vi.fn().mockResolvedValue({})
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

function tileNumbers(target: HTMLElement): (string | undefined)[] {
	return tiles(target).map((tile) => tile.querySelector('span')?.textContent?.trim());
}

function inspectorLabel(target: HTMLElement): HTMLInputElement {
	return target.querySelector<HTMLInputElement>('[data-media-inspector] input[type="text"]')!;
}

function buttonByLabel(root: ParentNode, label: string): HTMLButtonElement | undefined {
	return Array.from(root.querySelectorAll('button')).find((b) => b.getAttribute('aria-label') === label);
}

function buttonByText(root: ParentNode, text: string): HTMLButtonElement | undefined {
	return Array.from(root.querySelectorAll('button')).find((b) => (b.textContent || '').trim() === text);
}

async function openTools(target: HTMLElement): Promise<HTMLElement> {
	target.querySelector<HTMLButtonElement>('[data-tools-trigger]')!.click();
	await tick();
	await tick();
	return document.body.querySelector<HTMLElement>('[data-tools-menu]')!;
}

function toolIds(menu: HTMLElement): string[] {
	return Array.from(menu.querySelectorAll('[data-tool]')).map((el) => el.getAttribute('data-tool') as string);
}

async function pickTool(target: HTMLElement, id: string) {
	const menu = await openTools(target);
	menu.querySelector<HTMLButtonElement>(`[data-tool="${id}"]`)!.click();
	await tick();
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

		expect(target.textContent).toContain('Drop an image or');
		expect(target.textContent).toContain('PNG · JPG · WEBP');
		for (const door of ['Browse', 'Paste', 'History', 'Library', 'Draw']) {
			expect(buttonByText(target, door), door).toBeTruthy();
		}
	});

	it('offers the history door on an audio-only field but no paste', () => {
		const { target } = mount({
			name: 'voice',
			config: { title: 'Voice track', accept: 'audio/*' },
			value: null,
			onChange: vi.fn()
		});

		expect(buttonByText(target, 'History')).toBeTruthy();
		expect(buttonByText(target, 'Paste')).toBeUndefined();
		expect(target.textContent).toContain('Drop an audio file or');
	});

	it('says media when the field takes several kinds', () => {
		const { target } = mount({
			name: 'inputs',
			config: { title: 'Input media', accept: 'image/*,video/*' },
			value: [],
			onChange: vi.fn()
		});
		expect(target.textContent).toContain('Drop media or');
	});
});

describe('loaded face', () => {
	it('puts the file name, chips and the direct actions in one inspector', async () => {
		const { target } = mount({
			name: 'reference_image',
			config: { title: 'Reference image', accept: 'image/*' },
			value: imageItem('sdxl_portrait_0043'),
			onChange: vi.fn()
		});
		await tick();

		const inspector = target.querySelector('[data-media-inspector]');
		expect(inspector).toBeTruthy();
		expect(inspector!.textContent).toContain('sdxl_portrait_0043.png');
		expect(inspector!.textContent).toContain('1024×1024');
		expect(inspector!.textContent).toContain('PNG');
		expect(inspector!.textContent).toContain('2.3 MB');
		expect(buttonByLabel(inspector!, 'Replace')).toBeTruthy();
		expect(buttonByLabel(inspector!, 'Remove')).toBeTruthy();
		expect(target.querySelector('[data-media-strip]')).toBeNull();
	});

	it('has no title attribute on any control', async () => {
		const { target } = mount({
			name: 'reference_image',
			config: { title: 'Reference image', accept: 'image/*' },
			value: imageItem('a'),
			onChange: vi.fn()
		});
		await tick();
		expect(target.querySelectorAll('[title]')).toHaveLength(0);
	});

	it('removes the value with one click', async () => {
		const onChange = vi.fn();
		const { target } = mount({
			name: 'reference_image',
			config: { title: 'Reference image', accept: 'image/*' },
			value: imageItem('a'),
			onChange
		});
		await tick();
		buttonByLabel(target, 'Remove')!.click();
		expect(onChange).toHaveBeenCalledWith('reference_image', null);
	});

	it('lists the image tools in the Tools menu, and the mask tools only when the field allows inpainting', async () => {
		const plain = mount({
			name: 'reference_image',
			config: { title: 'Reference image', accept: 'image/*' },
			value: imageItem('a'),
			onChange: vi.fn()
		});
		await tick();
		expect(toolIds(await openTools(plain.target))).toEqual(['edit', 'crop', 'full']);

		document.body.innerHTML = '';
		const masked = mount({
			name: 'reference_image',
			config: { title: 'Reference image', accept: 'image/*', allow_inpaint: true },
			value: imageItem('a'),
			onChange: vi.fn(),
			onMaskChange: vi.fn()
		});
		await tick();
		expect(toolIds(await openTools(masked.target))).toEqual(['edit', 'crop', 'mask', 'clear-mask', 'full']);
	});

	it('offers trim on a video, and opens the built-in editor when no host intercepts', async () => {
		const withoutHost = mount({
			name: 'clip',
			config: { title: 'Source video', accept: 'video/*' },
			value: videoItem('wan22_i2v_00042'),
			onChange: vi.fn()
		});
		await tick();

		await pickTool(withoutHost.target, 'trim');
		expect(document.body.textContent).toContain('Trim in / out');

		document.body.innerHTML = '';
		const onOpenEditor = vi.fn();
		const withHost = mount({
			name: 'clip',
			config: { title: 'Source video', accept: 'video/*' },
			value: videoItem('wan22_i2v_00042'),
			onChange: vi.fn(),
			onOpenEditor
		});
		await tick();

		await pickTool(withHost.target, 'trim');
		expect(onOpenEditor).toHaveBeenCalledWith(
			expect.objectContaining({
				kind: 'trim',
				itemIndex: null,
				source: expect.objectContaining({ kind: 'video' })
			})
		);
	});

	it('runs a tool from its keyboard shortcut', async () => {
		const onOpenEditor = vi.fn();
		const { target } = mount({
			name: 'reference_image',
			config: { title: 'Reference image', accept: 'image/*' },
			value: imageItem('a'),
			onChange: vi.fn(),
			onOpenEditor
		});
		await tick();

		target
			.querySelector('[data-media-inspector]')!
			.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', bubbles: true, cancelable: true }));
		expect(onOpenEditor).toHaveBeenCalledWith(expect.objectContaining({ kind: 'crop' }));
	});

	it('peeks the single preview full size, as whichever kind it is', async () => {
		const { target } = mount({
			name: 'reference_image',
			config: { title: 'Reference image', accept: 'image/*' },
			value: imageItem('a'),
			onChange: vi.fn()
		});
		await tick();

		buttonByLabel(target, 'View full size')!.click();
		await tick();
		const dialog = document.body.querySelector('[aria-label="Media preview"]');
		expect(dialog!.querySelector('img')?.getAttribute('src')).toBe('/api/media/uploads/a.png');
	});
});

describe('rejection face', () => {
	it('refuses a kind the field does not take, before uploading anything', async () => {
		const onChange = vi.fn();
		const sendSpy = vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(() => {});
		const { target } = mount({
			name: 'reference_image',
			config: { title: 'Reference image', accept: 'image/*' },
			value: null,
			onChange
		});

		const file = new File(['x'], 'take_04.mov', { type: 'video/quicktime' });
		fireDrag(target.querySelector<HTMLElement>('[data-media-field]')!, 'drop', {
			types: ['Files'],
			files: [file]
		});
		await tick();
		await tick();

		expect(target.textContent).toContain(
			"Type 'video' is not accepted for 'reference_image' (accepted: image)"
		);
		expect(target.textContent).toContain('take_04.mov · video/quicktime');
		expect(onChange).not.toHaveBeenCalled();
		expect(sendSpy).not.toHaveBeenCalled();
		sendSpy.mockRestore();
	});

	it('refuses a file for a kind that is already at its own limit while another kind still has room', async () => {
		const sendSpy = vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(() => {});
		const { target } = mount({
			name: 'refs',
			config: {
				title: 'References',
				accepted_types: ['image', 'video'],
				multiple: true,
				max_items_by_kind: { image: 9, video: 1 }
			},
			value: [imageItem('a'), videoItem('v')],
			onChange: vi.fn()
		});
		await tick();

		fireDrag(target.querySelector<HTMLElement>('[data-media-field]')!, 'drop', {
			types: ['Files'],
			files: [new File(['x'], 'second.mp4', { type: 'video/mp4' })]
		});
		await tick();
		await tick();

		expect(target.textContent).toContain("Too many video items for 'refs': maximum is 1");
		expect(sendSpy).not.toHaveBeenCalled();
		sendSpy.mockRestore();
	});

	it('dismisses the explanation', async () => {
		const { target } = mount({
			name: 'reference_image',
			config: { title: 'Reference image', accept: 'image/*' },
			value: null,
			onChange: vi.fn()
		});
		fireDrag(target.querySelector<HTMLElement>('[data-media-field]')!, 'drop', {
			types: ['Files'],
			files: [new File(['x'], 'a.mov', { type: 'video/quicktime' })]
		});
		await tick();
		await tick();
		expect(target.querySelector('[data-media-rejection]')).toBeTruthy();

		buttonByLabel(target, 'Dismiss')!.click();
		await tick();
		expect(target.querySelector('[data-media-rejection]')).toBeNull();
	});
});

describe('multi face', () => {
	const multi = (extra: Record<string, unknown> = {}) => ({
		name: 'refs',
		config: { title: 'Reference images', accept: 'image/*', multiple: true, max_items: 6, ...extra },
		onChange: vi.fn()
	});

	it('shows one inspector for the selected item over a numbered strip', async () => {
		const { target } = mount({ ...multi(), value: [imageItem('a'), imageItem('b'), imageItem('c')] });
		await tick();

		expect(target.querySelectorAll('[data-media-inspector]')).toHaveLength(1);
		expect(tileNumbers(target)).toEqual(['1', '2', '3']);
		expect(target.querySelector('[data-media-count]')?.textContent?.trim()).toBe('3/6');
		expect(tiles(target)[0].getAttribute('data-selected')).toBe('true');
		expect(inspectorLabel(target).placeholder).toBe('a.png');
	});

	it('selects another item when its tile is pressed and shows that item in the inspector', async () => {
		const { target } = mount({ ...multi(), value: [imageItem('a'), imageItem('b'), imageItem('c')] });
		await tick();

		tiles(target)[2].click();
		await tick();
		expect(tiles(target)[2].getAttribute('data-selected')).toBe('true');
		expect(inspectorLabel(target).placeholder).toBe('c.png');
		expect(target.querySelector('[data-media-handle]')!.textContent).toContain('Picture 3');
	});

	it('clears the selection when the selected tile is pressed again', async () => {
		const { target } = mount({ ...multi(), value: [imageItem('a'), imageItem('b')] });
		await tick();

		tiles(target)[1].click();
		await tick();
		expect(tiles(target)[1].getAttribute('data-selected')).toBe('true');
		tiles(target)[1].click();
		await tick();
		expect(tiles(target).some((tile) => tile.hasAttribute('data-selected'))).toBe(false);
		expect(target.querySelector('[data-media-inspector]')).toBeNull();
	});

	it('clears the selection when neutral space is pressed and keeps it for the inspector', async () => {
		const { target } = mount({ ...multi(), value: [imageItem('a'), imageItem('b')] });
		await tick();

		const press = (el: Element) => el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
		press(target.querySelector('[data-media-inspector]')!);
		await tick();
		expect(tiles(target)[0].getAttribute('data-selected')).toBe('true');

		press(document.body);
		await tick();
		expect(tiles(target).some((tile) => tile.hasAttribute('data-selected'))).toBe(false);
	});

	it('writes a typed label onto the selected item', async () => {
		const props = { ...multi(), value: [imageItem('a'), imageItem('b')] };
		const { target } = mount(props);
		await tick();

		tiles(target)[1].click();
		await tick();
		const label = inspectorLabel(target);
		label.value = 'Main subject';
		label.dispatchEvent(new Event('input', { bubbles: true }));

		const written = props.onChange.mock.calls.at(-1)![1];
		expect(written[1].label).toBe('Main subject');
		expect(written[0]).not.toHaveProperty('label');
	});

	it('reorders to the slot an item was dropped on', async () => {
		const props = { ...multi(), value: [imageItem('a'), imageItem('b'), imageItem('c')] };
		const { target } = mount(props);
		await tick();

		const rendered = tiles(target);
		const dataTransfer = { setData: vi.fn(), effectAllowed: '' };
		fireDrag(rendered[0], 'dragstart', dataTransfer);
		fireDrag(rendered[2], 'dragover', dataTransfer);
		fireDrag(rendered[2], 'drop', dataTransfer);

		expect(props.onChange).toHaveBeenCalledTimes(1);
		expect(props.onChange.mock.calls[0][1].map((item: { name: string }) => item.name)).toEqual([
			'b.png',
			'c.png',
			'a.png'
		]);
	});

	it('moves an item with Alt and the arrow keys and keeps it selected', async () => {
		const props = { ...multi(), value: [imageItem('a'), imageItem('b'), imageItem('c')] };
		const { target } = mount(props);
		await tick();

		tiles(target)[0].dispatchEvent(
			new KeyboardEvent('keydown', { key: 'ArrowRight', altKey: true, bubbles: true, cancelable: true })
		);
		expect(props.onChange.mock.calls[0][1].map((item: { name: string }) => item.name)).toEqual([
			'b.png',
			'a.png',
			'c.png'
		]);
	});

	it('removes just the pressed item with Delete', async () => {
		const props = { ...multi(), value: [imageItem('a'), imageItem('b'), imageItem('c')] };
		const { target } = mount(props);
		await tick();

		tiles(target)[1].dispatchEvent(
			new KeyboardEvent('keydown', { key: 'Delete', bubbles: true, cancelable: true })
		);
		expect(props.onChange.mock.calls[0][1].map((item: { name: string }) => item.name)).toEqual(['a.png', 'c.png']);
	});

	it('lets Order tools in the menu do what Alt and the arrows do', async () => {
		const props = { ...multi(), value: [imageItem('a'), imageItem('b'), imageItem('c')] };
		const { target } = mount(props);
		await tick();

		const menu = await openTools(target);
		expect(menu.querySelector('[data-tool="earlier"]')!.getAttribute('aria-disabled')).toBe('true');
		menu.querySelector<HTMLButtonElement>('[data-tool="later"]')!.click();
		await tick();
		expect(props.onChange.mock.calls[0][1].map((item: { name: string }) => item.name)).toEqual([
			'b.png',
			'a.png',
			'c.png'
		]);
	});

	it('clears every item from the Source group of the Tools menu', async () => {
		const props = { ...multi(), value: [imageItem('a'), imageItem('b')] };
		const { target } = mount(props);
		await tick();
		await pickTool(target, 'remove-all');
		expect(props.onChange).toHaveBeenCalledWith('refs', []);
	});

	it('opens a tool against the selected item, by index', async () => {
		const onOpenEditor = vi.fn();
		const { target } = mount({
			...multi(),
			value: [imageItem('a'), imageItem('b'), imageItem('c')],
			onOpenEditor
		});
		await tick();

		tiles(target)[1].click();
		await tick();
		await pickTool(target, 'crop');

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

	it('drops the add tile at the cap and turns the count amber', async () => {
		const { target } = mount({ ...multi({ max_items: 2 }), value: [imageItem('a'), imageItem('b')] });
		await tick();

		expect(target.querySelector('[data-media-add]')).toBeNull();
		const count = target.querySelector('[data-media-count]')!;
		expect(count.textContent?.trim()).toBe('2/2');
		expect(count.className).toContain('text-warning');
		expect(target.textContent).not.toContain('slots used');
	});

	it('draws one add tile, not a placeholder per free slot', async () => {
		const { target } = mount({ ...multi({ max_items: 10 }), value: [imageItem('a')] });
		await tick();
		expect(target.querySelectorAll('[data-media-add]')).toHaveLength(1);
	});

	it('keeps the normal drop zone while nothing is held', async () => {
		const { target } = mount({ ...multi(), value: [] });
		await tick();
		expect(target.querySelector('[data-media-dropzone]')).toBeTruthy();
		expect(target.querySelector('[data-media-count]')?.textContent?.trim()).toBe('0/6');
	});

	it('peeks the selected item full size, as whichever kind it actually is', async () => {
		const { target } = mount({
			name: 'inputs',
			config: { title: 'Input media', accept: 'image/*,video/*,audio/*', multiple: true },
			value: [imageItem('a'), videoItem('v'), audioItem('s')],
			onChange: vi.fn()
		});
		await tick();

		async function peek(index: number) {
			if (!tiles(target)[index].hasAttribute('data-selected')) tiles(target)[index].click();
			await tick();
			await pickTool(target, 'full');
			return document.body.querySelector('[aria-label="Media preview"]');
		}

		let dialog = await peek(0);
		expect(dialog!.querySelector('img')?.getAttribute('src')).toBe('/api/media/uploads/a.png');
		dialog!.querySelector<HTMLButtonElement>('[aria-label="Close preview"]')!.click();
		await tick();

		dialog = await peek(1);
		expect(dialog!.querySelector('video')?.getAttribute('src')).toBe('/api/media/uploads/v.mp4');
		dialog!.querySelector<HTMLButtonElement>('[aria-label="Close preview"]')!.click();
		await tick();

		dialog = await peek(2);
		expect(dialog!.querySelector('img')).toBeNull();
		expect(dialog!.querySelector('video')).toBeNull();
		expect(dialog!.querySelector('audio')).toBeTruthy();
	});
});

describe('mixed kinds in one field', () => {
	const mixed = (value: unknown[], extra: Record<string, unknown> = {}) => ({
		name: 'refs',
		config: {
			title: 'References',
			accepted_types: ['image', 'video', 'audio'],
			multiple: true,
			max_items_by_kind: { image: 9, video: 3, audio: 3 },
			...extra
		},
		value,
		onChange: vi.fn()
	});

	it('draws one group per kind in image, video, audio order, each counting against its own limit', async () => {
		const { target } = mount(mixed([audioItem('s'), videoItem('v'), imageItem('a'), imageItem('b')]));
		await tick();

		const eyebrows = Array.from(target.querySelectorAll('[data-media-eyebrow]'));
		expect(eyebrows.map((el) => el.getAttribute('data-media-eyebrow'))).toEqual(['image', 'video', 'audio']);
		expect(eyebrows.map((el) => el.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
			'Images 2/9',
			'Videos 1/3',
			'Audio 1/3'
		]);
		expect(target.querySelector('[data-media-count]')?.textContent?.trim()).toBe('4/15');
	});

	it('numbers each kind from one', async () => {
		const { target } = mount(mixed([imageItem('a'), videoItem('v'), imageItem('b')]));
		await tick();
		expect(tileNumbers(target)).toEqual(['1', '2', '1']);
	});

	it('names the selected item by its own kind and position', async () => {
		const { target } = mount(mixed([imageItem('a'), videoItem('v'), imageItem('b')]));
		await tick();

		target.querySelector<HTMLElement>('[data-media-group="video"] [data-media-tile]')!.click();
		await tick();
		expect(target.querySelector('[data-media-inspector]')!.getAttribute('data-kind')).toBe('video');
		expect(target.querySelector('[data-media-handle]')!.textContent).toContain('Video 1');
	});

	it('draws a group with its own add box for every kind, empty kinds included', async () => {
		const { target } = mount(mixed([imageItem('a')]));
		await tick();

		expect(target.querySelector('[data-media-folded]')).toBeNull();
		for (const kind of ['image', 'video', 'audio']) {
			expect(target.querySelectorAll(`[data-media-group="${kind}"] [data-media-add="${kind}"]`), kind).toHaveLength(1);
		}
		const counts = Array.from(target.querySelectorAll('[data-media-group-count]')).map((el) => el.textContent?.trim());
		expect(counts).toEqual(['1/9', '0/3', '0/3']);
	});

	it('shows every group with its add box when a mixed field holds nothing, and no inspector', async () => {
		const { target } = mount(mixed([]));
		await tick();

		expect(target.querySelector('[data-media-inspector]')).toBeNull();
		expect(target.querySelector('[data-media-dropzone]')).toBeNull();
		expect(target.querySelectorAll('[data-media-add]')).toHaveLength(3);
	});

	it('opens the source menu for the kind an empty group names, with a history entry and no paste', async () => {
		const { target } = mount(mixed([imageItem('a')]));
		await tick();

		target.querySelector<HTMLButtonElement>('[data-media-add="audio"]')!.click();
		await tick();
		await tick();
		const menu = document.body.querySelector('[data-source-menu]')!;
		expect(Array.from(menu.querySelectorAll('[data-source]')).map((el) => el.getAttribute('data-source'))).toEqual([
			'browse',
			'history',
			'library'
		]);
	});

	it('gives each group its own add tile and takes it away from a full group only', async () => {
		const { target } = mount(mixed([imageItem('a'), videoItem('v')], { max_items_by_kind: { image: 9, video: 1, audio: 3 } }));
		await tick();
		expect(target.querySelector('[data-media-group="video"] [data-media-add]')).toBeNull();
		expect(target.querySelector('[data-media-group="image"] [data-media-add]')).toBeTruthy();
	});

	it('reorders within a group without disturbing the other kinds', async () => {
		const props = mixed([imageItem('a'), videoItem('v'), imageItem('b')]);
		const { target } = mount(props);
		await tick();

		const rendered = tiles(target);
		const dataTransfer = { setData: vi.fn(), effectAllowed: '' };
		fireDrag(rendered[0], 'dragstart', dataTransfer);
		fireDrag(rendered[1], 'dragover', dataTransfer);
		fireDrag(rendered[1], 'drop', dataTransfer);

		expect(props.onChange.mock.calls[0][1].map((item: { name: string }) => item.name)).toEqual([
			'b.png',
			'v.mp4',
			'a.png'
		]);
	});

	it('refuses a drop from one group onto another', async () => {
		const props = mixed([imageItem('a'), videoItem('v'), imageItem('b')]);
		const { target } = mount(props);
		await tick();

		const rendered = tiles(target);
		const dataTransfer = { setData: vi.fn(), effectAllowed: '' };
		fireDrag(rendered[0], 'dragstart', dataTransfer);
		fireDrag(rendered[2], 'drop', dataTransfer);

		expect(props.onChange).not.toHaveBeenCalled();
	});
});

describe('compact face', () => {
	it('shows a single item as one row with its tools behind an overflow menu', async () => {
		const { target } = mount({
			name: 'reference_image',
			config: { title: 'Reference image', accept: 'image/*' },
			value: imageItem('a'),
			compact: true,
			onChange: vi.fn()
		});
		await tick();

		expect(target.querySelector('[data-media-row-face]')).toBeTruthy();
		expect(target.querySelector('[data-media-inspector]')).toBeNull();
		expect(toolIds(await openTools(target))).toEqual(['edit', 'crop', 'full', 'replace', 'remove']);
	});

	it('shows an empty compact field as a split add button', async () => {
		const { target } = mount({
			name: 'reference_image',
			config: { title: 'Reference image', accept: 'image/*' },
			value: null,
			compact: true,
			onChange: vi.fn()
		});
		await tick();
		expect(buttonByText(target, 'Add image')).toBeTruthy();
		expect(buttonByLabel(target, 'More ways to add')).toBeTruthy();
	});

	it('lists a multi field as rows and reorders with Alt and the arrow keys on the grip', async () => {
		const props = {
			name: 'refs',
			config: { title: 'Reference images', accept: 'image/*', multiple: true },
			value: [imageItem('a'), imageItem('b')],
			compact: true,
			onChange: vi.fn()
		};
		const { target } = mount(props);
		await tick();

		expect(target.querySelectorAll('[data-media-row]')).toHaveLength(2);
		target
			.querySelector('[data-media-grip="0"]')!
			.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));
		expect(props.onChange.mock.calls[0][1].map((item: { name: string }) => item.name)).toEqual(['b.png', 'a.png']);
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

		buttonByText(target, 'Library')!.click();
		await tick();
		await tick();
		await tick();

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
		expect(onChange.mock.calls[0][1][0]).not.toHaveProperty('label');
	});
});
