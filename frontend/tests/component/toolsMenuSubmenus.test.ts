// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { createClassComponent } from 'svelte/legacy';
import { settle } from './helpers/settle';
import { emulateSyncFocusOutOnRemove } from './helpers/syncFocusOut';
import { keyboardFocus } from './helpers/keyboardFocus';

vi.mock('$lib/tools/pluginTools', () => ({ retryMediaToolCatalog: vi.fn() }));

import ToolsMenu from '$lib/components/tools/ToolsMenu.svelte';
import type { MediaTool, MediaToolAvailability, MediaToolGroup } from '$lib/tools/tools';

function tool(id: string, extra: Partial<MediaTool> = {}): MediaTool {
	return {
		id,
		label: id.toUpperCase(),
		icon: 'extension',
		category: 'edit',
		source: 'core',
		scopes: ['history', 'library'],
		applies: () => ({ enabled: true }),
		...extra
	};
}

type Entry = { tool: MediaTool; availability: MediaToolAvailability };
const on = (id: string, extra: Partial<MediaTool> = {}): Entry => ({ tool: tool(id, extra), availability: { enabled: true } });
const off = (id: string, reason: string, extra: Partial<MediaTool> = {}): Entry => ({
	tool: tool(id, extra),
	availability: { enabled: false, reason }
});

function group(id: string, label: string, order: number, tools: Entry[]): MediaToolGroup {
	return { category: { id, label, order }, tools };
}

const edit = (tools: Entry[]) => group('edit', 'Edit', -20, tools);
const effects = (tools: Entry[]) => group('effects', 'Effects', -10, tools);
const compose = () => group('compose', 'Compose', 20, [on('stitch'), on('edit-image', { category: 'compose', shortcut: 'E' })]);
const analyze = () => group('analyze', 'Analyze', 10, [off('compare', 'Select exactly 2 items', { category: 'analyze' })]);

const IMAGE_GROUPS = [
	edit([on('crop'), on('resize'), on('rotate'), off('crop_subject', 'Needs a transparent image')]),
	effects([on('apply-look', { category: 'effects', label: 'Apply a look…' }), on('look:noir', { category: 'effects', note: 'Black and white' })]),
	analyze(),
	compose()
];

let cleanup: (() => void) | undefined;

function render(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const handlers = { onToggle: vi.fn(), onClose: vi.fn(), onPick: vi.fn() };
	const instance = mount(ToolsMenu, {
		target,
		props: { open: true, scope: 'library', groups: IMAGE_GROUPS, ...handlers, ...props }
	});
	flushSync();
	cleanup = () => {
		unmount(instance);
		target.remove();
	};
	return { target, ...handlers };
}

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

const menu = () => document.body.querySelector<HTMLElement>('[data-tools-menu]')!;
const row = (id: string) => menu().querySelector<HTMLButtonElement>(`[data-submenu-row="${id}"]`);
const rows = () => Array.from(menu().querySelectorAll('[data-submenu-row]')).map((el) => el.getAttribute('data-submenu-row'));
const back = () => menu().querySelector<HTMLButtonElement>('[data-submenu-back]');
const toolIds = () => Array.from(menu().querySelectorAll('[data-tool]')).map((el) => el.getAttribute('data-tool'));
const groupLabels = () => Array.from(menu().querySelectorAll('[role="group"]')).map((el) => el.getAttribute('aria-label'));
const tool$ = (id: string) => menu().querySelector<HTMLButtonElement>(`[data-tool="${id}"]`)!;

async function frames() {
	await settle(3);
	await new Promise((resolve) => setTimeout(resolve, 40));
	flushSync();
}

async function openRow(id: string) {
	row(id)!.click();
	flushSync();
	await frames();
}

function key(target: Element, name: string) {
	const event = new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true });
	target.dispatchEvent(event);
	flushSync();
	return event;
}

describe('the top level', () => {
	it('puts an Edit row and an Effects row first, each with an icon and a chevron', () => {
		render();

		expect(rows()).toEqual(['edit', 'effects']);
		const first = menu().querySelector('[role="menuitem"]')!;
		expect(first).toBe(row('edit'));
		for (const id of ['edit', 'effects']) {
			expect(row(id)!.querySelectorAll('svg').length).toBe(2);
			expect(row(id)!.getAttribute('aria-haspopup')).toBe('menu');
		}
	});

	it('shows how many entries each submenu holds, in the mono tabular style', () => {
		render();

		const count = (id: string) => row(id)!.querySelector('[data-submenu-count]')!;
		expect(count('edit').textContent).toBe('4');
		expect(count('effects').textContent).toBe('2');
		expect(count('edit').className).toContain('font-mono');
		expect(count('edit').className).toContain('tabular-nums');
		expect(count('edit').className).toContain('text-[12px]');
	});

	it('keeps the tools of Edit and Effects out of the top level', () => {
		render();

		expect(toolIds()).not.toContain('crop');
		expect(toolIds()).not.toContain('apply-look');
		expect(groupLabels()).not.toContain('Edit');
		expect(groupLabels()).not.toContain('Effects');
	});

	it('leaves the other groups as they were, after the two rows', () => {
		render();

		expect(groupLabels()).toEqual(['Analyze', 'Compose']);
		expect(toolIds()).toEqual(['compare', 'stitch', 'edit-image']);
		const all = Array.from(menu().querySelectorAll('[data-submenu-row], [role="group"]'));
		expect(all.map((el) => el.getAttribute('data-submenu-row') ?? el.getAttribute('aria-label'))).toEqual([
			'edit',
			'effects',
			'Analyze',
			'Compose'
		]);
	});

	it('hides a row whose submenu would be empty', () => {
		render({ groups: [edit([on('trim')]), compose()] });
		expect(rows()).toEqual(['edit']);
		cleanup?.();
		document.body.innerHTML = '';

		render({ groups: [effects([on('apply-look', { category: 'effects' })]), compose()] });
		expect(rows()).toEqual(['effects']);
		cleanup?.();
		document.body.innerHTML = '';

		render({ groups: [compose()] });
		expect(rows()).toEqual([]);
	});

	it('hides both rows for a video-like selection that has no Effects, and only Effects goes', () => {
		render({ groups: [edit([on('trim'), on('crop')]), analyze()] });

		expect(rows()).toEqual(['edit']);
		expect(row('effects')).toBeNull();
	});

	it('still uses a submenu when it would hold a single entry', () => {
		render({ groups: [effects([on('apply-look', { category: 'effects' })])] });

		expect(row('effects')!.querySelector('[data-submenu-count]')!.textContent).toBe('1');
		expect(toolIds()).toEqual([]);
	});

	it('counts disabled entries too, because they are shown in the submenu', () => {
		render({ groups: [edit([off('crop_subject', 'Needs a transparent image')])] });

		expect(row('edit')!.querySelector('[data-submenu-count]')!.textContent).toBe('1');
	});

	it('says there is nothing to offer when no group applies', () => {
		render({ groups: [] });

		expect(menu().textContent).toContain('No tools for this selection');
	});

	it('leaves a field menu flat, as it was', () => {
		render({ scope: 'field' });

		expect(rows()).toEqual([]);
		expect(toolIds()).toContain('crop');
		expect(groupLabels()).toEqual(['Edit', 'Effects', 'Analyze', 'Compose']);
	});

	it('shows the same rows in the phone sheet', () => {
		render({ sheet: true, title: 'clip.png' });

		const sheet = document.body.querySelector('[data-tools-sheet]')!;
		expect(sheet.querySelector('[data-submenu-row="edit"]')).toBeTruthy();
		expect(sheet.querySelector('[data-submenu-row="effects"]')).toBeTruthy();
		expect(sheet.querySelector('[data-tool="crop"]')).toBeNull();
	});
});

describe('opening a submenu with the mouse', () => {
	it('swaps the panel for a back row and the entries of that submenu', async () => {
		render();

		await openRow('edit');

		expect(back()!.textContent!.trim()).toBe('Edit');
		expect(back()!.querySelector('svg')).toBeTruthy();
		expect(toolIds()).toEqual(['crop', 'resize', 'rotate', 'crop_subject']);
		expect(rows()).toEqual([]);
		expect(groupLabels()).toEqual(['Edit']);
	});

	it('lists Apply a look first and then the recent looks under Effects', async () => {
		render();

		await openRow('effects');

		expect(back()!.textContent!.trim()).toBe('Effects');
		expect(toolIds()).toEqual(['apply-look', 'look:noir']);
	});

	it('keeps notes, disabled reasons and the greyed look inside the submenu', async () => {
		render();

		await openRow('edit');

		expect(tool$('crop_subject').getAttribute('aria-disabled')).toBe('true');
		expect(tool$('crop_subject').querySelector('[data-tool-sub]')!.textContent).toBe('Needs a transparent image');
		expect(tool$('crop_subject').className).toContain('cursor-not-allowed');
	});

	it('shows a note under an entry of Effects', async () => {
		render();

		await openRow('effects');

		expect(tool$('look:noir').querySelector('[data-tool-sub]')!.textContent).toBe('Black and white');
	});

	it('puts plugin tools after the core ones under a divider, naming the plugin', async () => {
		render({
			groups: [edit([on('crop'), on('rotate'), on('boomerang.loop', { source: 'plugin', note: 'Boomerang' })])]
		});

		await openRow('edit');

		const body = menu().querySelector('[data-submenu-group]')!;
		const sequence = Array.from(body.children).map((el) => el.getAttribute('data-submenu-divider') !== null ? 'divider' : el.querySelector('[data-tool]')?.getAttribute('data-tool'));
		expect(sequence).toEqual(['crop', 'rotate', 'divider', 'boomerang.loop']);
		expect(tool$('boomerang.loop').querySelector('[data-tool-sub]')!.textContent).toBe('Boomerang');
	});

	it('keeps plugin tools after the core ones even when they were listed first', async () => {
		render({ groups: [edit([on('boomerang.loop', { source: 'plugin', note: 'Boomerang' }), on('crop')])] });

		await openRow('edit');

		expect(toolIds()).toEqual(['crop', 'boomerang.loop']);
	});

	it('draws no divider when there are only core tools, or only plugin tools', async () => {
		render({ groups: [edit([on('crop'), on('rotate')])] });
		await openRow('edit');
		expect(menu().querySelector('[data-submenu-divider]')).toBeNull();
		cleanup?.();
		document.body.innerHTML = '';

		render({ groups: [edit([on('boomerang.loop', { source: 'plugin', note: 'Boomerang' })])] });
		await openRow('edit');
		expect(menu().querySelector('[data-submenu-divider]')).toBeNull();
	});

	it('runs a tool picked in the submenu and closes the whole menu', async () => {
		const { onPick, onClose } = render();
		await openRow('edit');

		tool$('rotate').click();

		expect(onClose).toHaveBeenCalledTimes(1);
		expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 'rotate' }));
	});

	it('does nothing for a disabled entry, which stays in the submenu', async () => {
		const { onPick, onClose } = render();
		await openRow('edit');

		tool$('crop_subject').click();

		expect(onPick).not.toHaveBeenCalled();
		expect(onClose).not.toHaveBeenCalled();
		expect(back()).toBeTruthy();
	});

	it('returns to the top level from the back row', async () => {
		const { onClose } = render();
		await openRow('edit');

		back()!.click();
		await frames();

		expect(rows()).toEqual(['edit', 'effects']);
		expect(back()).toBeNull();
		expect(onClose).not.toHaveBeenCalled();
	});

	it('puts focus back on the row that opened the submenu', async () => {
		render();
		await openRow('effects');

		back()!.click();
		await frames();

		expect(document.activeElement).toBe(row('effects'));
	});

	it('can open the other submenu after coming back', async () => {
		render();
		await openRow('edit');
		back()!.click();
		await frames();

		await openRow('effects');

		expect(toolIds()).toEqual(['apply-look', 'look:noir']);
	});

	it('starts at the top level again the next time the menu opens', async () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const instance = createClassComponent({
			component: ToolsMenu as never,
			target,
			props: { open: true, scope: 'library', groups: IMAGE_GROUPS, onToggle: vi.fn(), onClose: vi.fn(), onPick: vi.fn() }
		});
		cleanup = () => {
			instance.$destroy();
			target.remove();
		};
		flushSync();
		await openRow('edit');

		instance.$set({ open: false });
		flushSync();
		instance.$set({ open: true });
		flushSync();

		expect(rows()).toEqual(['edit', 'effects']);
		expect(back()).toBeNull();
	});

	it('works the same in the phone sheet', async () => {
		render({ sheet: true, title: 'clip.png' });

		await openRow('edit');
		const sheet = document.body.querySelector('[data-tools-sheet]')!;

		expect(sheet.querySelector('[data-submenu-back]')!.textContent!.trim()).toBe('Edit');
		expect(sheet.querySelector('[data-tool="crop"]')).toBeTruthy();
		sheet.querySelector<HTMLButtonElement>('[data-submenu-back]')!.click();
		await frames();
		expect(sheet.querySelector('[data-submenu-row="edit"]')).toBeTruthy();
	});
});

describe('opening a submenu with the keyboard', () => {
	it('opens on a press of a submenu row, which is a button so Enter and Space click it', async () => {
		render();
		await frames();

		expect(row('edit')!.tagName).toBe('BUTTON');
		expect(row('edit')!.getAttribute('type')).toBe('button');
		row('edit')!.focus();
		row('edit')!.click();
		flushSync();
		await frames();

		expect(back()).toBeTruthy();
	});

	it('opens on ArrowRight and focuses the first enabled entry, not the back row', async () => {
		render();
		await frames();
		row('edit')!.focus();

		key(row('edit')!, 'ArrowRight');
		await frames();

		expect(back()).toBeTruthy();
		expect(document.activeElement).toBe(tool$('crop'));
	});

	it('skips disabled entries when it picks what to focus', async () => {
		render({ groups: [edit([off('crop_subject', 'Needs a transparent image'), on('rotate')])] });
		await frames();
		row('edit')!.focus();

		key(row('edit')!, 'ArrowRight');
		await frames();

		expect(document.activeElement).toBe(tool$('rotate'));
	});

	it('focuses the back row when every entry is disabled', async () => {
		render({ groups: [edit([off('crop_subject', 'Needs a transparent image')])] });
		await frames();
		row('edit')!.focus();

		key(row('edit')!, 'ArrowRight');
		await frames();

		expect(document.activeElement).toBe(back());
	});

	it('opens the Effects row on ArrowRight as well', async () => {
		render();
		await frames();
		row('effects')!.focus();

		key(row('effects')!, 'ArrowRight');
		await frames();

		expect(document.activeElement).toBe(tool$('apply-look'));
	});

	it('ignores ArrowRight on a row that is not a submenu', async () => {
		render();
		await frames();
		const stitch = tool$('stitch');
		stitch.focus();

		const event = key(stitch, 'ArrowRight');

		expect(event.defaultPrevented).toBe(false);
		expect(back()).toBeNull();
	});

	it('goes back on ArrowLeft and focuses the row that opened it', async () => {
		const { onClose } = render();
		await frames();
		row('effects')!.focus();
		key(row('effects')!, 'ArrowRight');
		await frames();

		key(document.activeElement!, 'ArrowLeft');
		await frames();

		expect(rows()).toEqual(['edit', 'effects']);
		expect(document.activeElement).toBe(row('effects'));
		expect(onClose).not.toHaveBeenCalled();
	});

	it('goes back on Escape, keeps the menu open and focuses the row that opened it', async () => {
		const { onClose } = render();
		await frames();
		row('edit')!.focus();
		key(row('edit')!, 'ArrowRight');
		await frames();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
		flushSync();
		await frames();

		expect(onClose).not.toHaveBeenCalled();
		expect(rows()).toEqual(['edit', 'effects']);
		expect(document.activeElement).toBe(row('edit'));
	});

	it('closes the menu on Escape at the top level, as before', async () => {
		const { onClose } = render();
		await frames();
		row('edit')!.focus();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
		flushSync();

		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('closes only on the second Escape after a submenu', async () => {
		const { onClose } = render();
		await frames();
		row('edit')!.focus();
		key(row('edit')!, 'ArrowRight');
		await frames();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
		flushSync();
		await frames();
		expect(onClose).not.toHaveBeenCalled();
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
		flushSync();

		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('does nothing on ArrowLeft at the top level', async () => {
		const { onClose } = render();
		await frames();
		row('edit')!.focus();

		const event = key(row('edit')!, 'ArrowLeft');
		await frames();

		expect(event.defaultPrevented).toBe(false);
		expect(onClose).not.toHaveBeenCalled();
		expect(rows()).toEqual(['edit', 'effects']);
	});

	it('walks the submenu with the arrow keys, from the back row through the entries and round', async () => {
		render({ groups: [edit([on('crop'), on('rotate')])] });
		await frames();
		row('edit')!.focus();
		key(row('edit')!, 'ArrowRight');
		await frames();

		key(document.activeElement!, 'ArrowDown');
		expect(document.activeElement).toBe(tool$('rotate'));
		key(document.activeElement!, 'ArrowDown');
		expect(document.activeElement).toBe(back());
		key(document.activeElement!, 'ArrowDown');
		expect(document.activeElement).toBe(tool$('crop'));
	});

	it('does not run a tool shortcut while a submenu is open', async () => {
		const { onPick } = render();
		await frames();
		row('edit')!.focus();
		key(row('edit')!, 'ArrowRight');
		await frames();

		document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'e', bubbles: true, cancelable: true }));

		expect(onPick).not.toHaveBeenCalled();
	});

	it('still runs a tool shortcut from the top level', async () => {
		const { onPick } = render({ variant: 'bar' });
		await frames();

		document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'e', bubbles: true, cancelable: true }));

		expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 'edit-image' }));
	});

	it('focuses the first row on open, which is now the Edit row', async () => {
		render();
		await frames();
		await frames();

		expect(document.activeElement).toBe(row('edit'));
	});
});

describe('the collection submenu keeps working beside them', () => {
	const withCollection = [
		...IMAGE_GROUPS,
		group('organize', 'Organize', 40, [on('add-to-collection', { category: 'organize', control: 'collection' })])
	];

	it('opens on ArrowRight and returns with focus on its row on Escape', async () => {
		render({ groups: withCollection, collections: [{ id: 'c1', name: 'Boats' }] });
		await frames();
		tool$('add-to-collection').focus();

		key(tool$('add-to-collection'), 'ArrowRight');
		await frames();
		expect(menu().querySelector('[data-collection-id="c1"]')).toBeTruthy();
		expect(back()!.textContent!.trim()).toBe('Add to collection');

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
		flushSync();
		await frames();

		expect(rows()).toEqual(['edit', 'effects']);
		expect(document.activeElement).toBe(tool$('add-to-collection'));
	});

	it('still opens on a click and focuses the back row', async () => {
		render({ groups: withCollection, collections: [{ id: 'c1', name: 'Boats' }] });
		await frames();

		tool$('add-to-collection').click();
		flushSync();
		await frames();

		expect(document.activeElement).toBe(back());
	});
});

describe('leaving a submenu while a tool row holds focus', () => {
	let restore: (() => void) | undefined;
	let uncaught: unknown[] = [];
	const collect = (event: ErrorEvent) => {
		uncaught.push(event.error ?? event.message);
		event.preventDefault();
	};

	beforeEach(() => {
		uncaught = [];
		window.addEventListener('error', collect);
		restore = emulateSyncFocusOutOnRemove();
	});

	afterEach(() => {
		restore?.();
		window.removeEventListener('error', collect);
	});

	async function openEditWithFocusedTool() {
		const view = render();
		await frames();
		row('edit')!.focus();
		key(row('edit')!, 'ArrowRight');
		await frames();
		keyboardFocus(tool$('crop'));
		await frames();
		expect(document.activeElement).toBe(tool$('crop'));
		return view;
	}

	it('raises no state error on Escape and puts focus on the opening row', async () => {
		await openEditWithFocusedTool();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
		flushSync();
		await frames();

		expect(uncaught).toEqual([]);
		expect(rows()).toEqual(['edit', 'effects']);
		expect(document.activeElement).toBe(row('edit'));
	});

	it('raises no state error on ArrowLeft and puts focus on the opening row', async () => {
		await openEditWithFocusedTool();

		key(document.activeElement!, 'ArrowLeft');
		await frames();

		expect(uncaught).toEqual([]);
		expect(rows()).toEqual(['edit', 'effects']);
		expect(document.activeElement).toBe(row('edit'));
	});
});
