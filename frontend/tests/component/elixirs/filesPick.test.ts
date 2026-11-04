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

const assets = ['a', 'b', 'c', 'd'].map((item_id) => ({ kind: 'library', item_id, media_type: 'image', original_filename: `${item_id}.png`, preview_url: `/m/${item_id}.png` }));

function photos(extra: Record<string, unknown> = {}) {
	return { id: 'photos', kind: 'media', title: 'Photos', media_type: 'image', assets, ...extra };
}

function renderPanel(node: Record<string, unknown>) {
	const onfileschange = vi.fn();
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(NodePanel, {
		target,
		props: {
			node: node as never,
			client: {} as never,
			label: 'Photos',
			presets: [],
			onrename: () => {},
			onsettingschange: () => {},
			onchoosepreset: () => {},
			onpickmedia: () => {},
			onremoveasset: () => {},
			onfileschange
		}
	});
	flushSync();
	return onfileschange;
}

function renderCard({ node, ...data }: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(NodeCard, {
		target,
		props: {
			data: {
				node: photos(node as Record<string, unknown>),
				label: 'Photos',
				countLine: '4 images',
				sourceLine: 'From the Library',
				assets,
				slots: [],
				onopennode: () => {},
				onsettings: () => {},
				onopen: () => {},
				...data
			}
		}
	});
	flushSync();
}

it('a files step uses all its files until it is set to draw at random', () => {
	const onfileschange = renderPanel(photos());
	const group = target.querySelector('[role="group"][aria-label="Which files each brew uses"]')!;
	const [all, random] = [...group.querySelectorAll('button')];

	expect(all.getAttribute('aria-pressed')).toBe('true');
	expect(target.querySelector('input[aria-label="How many files to draw"]')).toBeNull();
	random.click();
	expect(onfileschange).toHaveBeenLastCalledWith({ pick: 'random' });
});

it('a random pick draws between one and every file the step holds', () => {
	const onfileschange = renderPanel(photos({ pick: 'random', pick_count: 3 }));
	const input = target.querySelector<HTMLInputElement>('input[aria-label="How many files to draw"]')!;

	expect(input.value).toBe('3');
	expect(target.textContent).toContain('Each brew draws 3 files of 4 at random.');
	target.querySelector<HTMLButtonElement>('button[aria-label="Increase How many files to draw"]')!.click();
	expect(onfileschange).toHaveBeenLastCalledWith({ pick_count: 4 });

	input.value = '40';
	input.dispatchEvent(new Event('input', { bubbles: true }));
	input.dispatchEvent(new Event('blur'));
	expect(onfileschange).toHaveBeenLastCalledWith({ pick_count: 4 });
});

it('the files card shows the random pick and dims the files this brew did not draw', () => {
	renderCard({ node: { pick: 'random', pick_count: 2 }, pickLine: 'Random 2 of 4', drawn: new Set(['library:b', 'library:d']) });

	expect(target.querySelector('[data-testid="elixir-files-pick"]')?.textContent).toContain('Random 2 of 4');
	const tiles = [...target.querySelectorAll('[role="listitem"]')];
	expect(tiles.map((tile) => tile.getAttribute('data-drawn'))).toEqual(['false', 'true', 'false', 'true']);
	expect(tiles.filter((tile) => tile.classList.contains('undrawn'))).toHaveLength(2);
});

it('the files card stays as it was when every file is used', () => {
	renderCard({ pickLine: null, drawn: null });

	expect(target.querySelector('[data-testid="elixir-files-pick"]')).toBeNull();
	expect([...target.querySelectorAll('[role="listitem"]')].some((tile) => tile.hasAttribute('data-drawn') || tile.classList.contains('undrawn'))).toBe(false);
});
