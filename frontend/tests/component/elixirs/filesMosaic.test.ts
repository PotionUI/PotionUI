import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const { default: NodePanel } = await import('$lib/components/elixirs/NodePanel.svelte');
const { default: NodeCard } = await import('$lib/components/elixirs/NodeCard.svelte');

let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	target?.remove();
});

const ratios = [1, 1.5, 0.75, 1.78, 0.56, 2];

function files(count: number) {
	return Array.from({ length: count }, (_, index) => ({
		kind: 'library',
		item_id: `f${index}`,
		media_type: 'image',
		original_filename: `f${index}.png`,
		preview_url: `/m/f${index}.png`,
		width: Math.round(ratios[index % ratios.length] * 800),
		height: 800
	}));
}

function node(assets: unknown[], extra: Record<string, unknown> = {}) {
	return { id: 'photos', kind: 'media', title: 'Photos', media_type: 'image', assets, ...extra };
}

function renderCard(assets: unknown[], data: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	const onopennode = vi.fn();
	component = mount(NodeCard, {
		target,
		props: {
			data: {
				node: node(assets),
				label: 'Photos',
				countLine: `${assets.length} images`,
				sourceLine: 'From the Library',
				assets,
				slots: [],
				onopennode,
				onsettings: () => {},
				onopen: () => {},
				...data
			}
		}
	});
	flushSync();
	return onopennode;
}

function renderPanel(assets: unknown[], extra: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	const onupload = vi.fn();
	component = mount(NodePanel, {
		target,
		props: {
			node: node(assets) as never,
			client: {} as never,
			label: 'Photos',
			presets: [],
			onrename: () => {},
			onsettingschange: () => {},
			onchoosepreset: () => {},
			onpickmedia: () => {},
			onremoveasset: () => {},
			onupload,
			...extra
		}
	});
	flushSync();
	return onupload;
}

const tiles = () => target.querySelectorAll('[role="listitem"]');

function paste(element: Element, list: File[]) {
	const event = new Event('paste', { bubbles: true, cancelable: true });
	Object.defineProperty(event, 'clipboardData', { value: { files: list, items: [] } });
	element.dispatchEvent(event);
	return event;
}

function drop(element: Element, list: File[]) {
	const event = new Event('drop', { bubbles: true, cancelable: true });
	Object.defineProperty(event, 'dataTransfer', { value: { types: ['Files'], files: list } });
	element.dispatchEvent(event);
}

it('a card with a thousand files renders a bounded preview and offers to show them all', () => {
	const onopennode = renderCard(files(1000));

	expect(tiles().length).toBeGreaterThan(0);
	expect(tiles().length).toBeLessThan(40);
	expect(target.querySelectorAll('img').length).toBe(tiles().length);
	expect([...target.querySelectorAll('img')].every((img) => img.getAttribute('loading') === 'lazy')).toBe(true);
	const more = [...target.querySelectorAll('button')].find((button) => button.textContent?.includes('Show all 1,000 files'))!;
	more.click();
	expect(onopennode).toHaveBeenCalledWith('photos');
});

it('a card whose files all fit has no show-all control', () => {
	renderCard(files(4));

	expect(tiles()).toHaveLength(4);
	expect([...target.querySelectorAll('button')].some((button) => button.textContent?.includes('Show all'))).toBe(false);
});

it('the random pick still dims undrawn files and brings the drawn ones into a long preview', () => {
	renderCard(files(500), { drawn: new Set(['library:f450']), pickLine: 'Random 1 of 500' });

	const first = tiles()[0];
	expect(first.getAttribute('data-drawn')).toBe('true');
	expect(tiles()[1].classList.contains('undrawn')).toBe(true);
	expect(target.textContent).toContain('Random 1 of 500');
});

it('the drawer shows more files as the admin asks for them', () => {
	renderPanel(files(1000));
	const before = tiles().length;

	expect(before).toBeLessThan(120);
	const more = [...target.querySelectorAll('button')].find((button) => button.textContent?.includes('Show more'))!;
	expect(more.textContent).toContain('1,000');
	more.click();
	flushSync();

	expect(tiles().length).toBeGreaterThan(before);
});

it('the drawer removes the file at the tile position', () => {
	const onremoveasset = vi.fn();
	renderPanel(files(5), { onremoveasset });

	target.querySelector<HTMLButtonElement>('button[aria-label="Remove f2.png"]')!.click();

	expect(onremoveasset).toHaveBeenCalledWith(2);
});

it('chosen files in the upload button go to the step', () => {
	const onupload = renderPanel(files(1));
	const input = target.querySelector<HTMLInputElement>('input[type="file"]')!;
	const picked = new File(['x'], 'a.png', { type: 'image/png' });
	Object.defineProperty(input, 'files', { value: [picked], configurable: true });

	input.dispatchEvent(new Event('change', { bubbles: true }));

	expect(onupload).toHaveBeenCalledWith([picked]);
	expect(input.getAttribute('accept')).toBe('image/*');
});

it('files pasted while the drawer is focused are sent to the step', () => {
	const onupload = renderPanel(files(1));
	const picked = new File(['x'], 'shot.png', { type: 'image/png' });

	const event = paste(target.querySelector('button')!, [picked]);

	expect(event.defaultPrevented).toBe(true);
	expect(onupload).toHaveBeenCalledTimes(1);
	expect(onupload.mock.calls[0][0][0].name).toMatch(/^shot\.png$/);
});

it('text pasted into a field of the drawer is left alone', () => {
	const onupload = renderPanel(files(1));
	const field = document.createElement('input');
	target.querySelector('aside')!.appendChild(field);

	paste(field, [new File(['x'], 'a.png', { type: 'image/png' })]);

	expect(onupload).not.toHaveBeenCalled();
});

it('files dropped on the drawer and on the card are uploaded', () => {
	const onupload = renderPanel(files(1));
	const dropped = new File(['x'], 'd.png', { type: 'image/png' });
	drop(target.querySelector('aside')!, [dropped]);
	expect(onupload).toHaveBeenCalledWith([dropped]);

	unmount(component!);
	target.remove();
	const onfiles = vi.fn();
	renderCard(files(2), { onfiles });
	drop(target.querySelector('article')!, [dropped]);
	paste(target.querySelector('article')!, [dropped]);
	expect(onfiles).toHaveBeenCalledTimes(2);
	expect(onfiles.mock.calls[0][0]).toBe('photos');
});

it('a card without an upload handler ignores pasted files', () => {
	renderCard(files(2));

	const event = paste(target.querySelector('article')!, [new File(['x'], 'a.png', { type: 'image/png' })]);

	expect(event.defaultPrevented).toBe(false);
});

it('upload progress and per-file errors are listed in the drawer and summarised on the card', () => {
	const items = [
		{ id: 0, name: 'a.png', status: 'done', error: null },
		{ id: 1, name: 'notes.txt', status: 'error', error: 'Only images and videos can be added to a files step.' },
		{ id: 2, name: 'b.png', status: 'uploading', error: null }
	];
	renderPanel(files(1), { upload: items });
	expect(target.querySelector('[data-testid="elixir-files-upload"]')?.textContent).toContain('Adding 1 of 3');
	expect(target.textContent).toContain('notes.txt');
	expect(target.textContent).toContain('Only images and videos');
	unmount(component!);
	target.remove();

	renderCard(files(1), { upload: items.slice(0, 2) });
	expect(target.querySelector('[data-testid="elixir-files-upload"]')?.textContent).toContain('1 of 2 not added');
});
