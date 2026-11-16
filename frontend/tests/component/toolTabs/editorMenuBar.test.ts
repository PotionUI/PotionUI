import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, tick } from 'svelte';
import { get } from 'svelte/store';
import { mountComponent } from '../helpers/mount';
import { isRowDisabled, menuRow, openEditorMenu } from '../helpers/editorMenu';
import { menuContext } from '../helpers/menuContext';
import { ensureCoreEditorMenu } from '$lib/editors/coreMenu';
import { menuItems, type EditorMenuContext } from '$lib/editors/menuItems';
import { railFor } from '$lib/editors/railTools';
import { pluginOwner } from '$lib/editors/registry';
import { settingsPaneWidth } from '$lib/stores/generationLayout';
import { viewportWidth } from '$lib/stores/viewport';
import { RAIL_ICON_WIDTH } from '$lib/tool-tabs/frame/layout';
import { ensureCoreImageEditor } from '$lib/tool-tabs/image/coreEditor';

const { default: MenuFrameHost } = await import('./MenuFrameHost.svelte');
const { default: EditorMenuSheet } = await import('$lib/components/tool-tabs/frame/EditorMenuSheet.svelte');

function text(el: Element | null | undefined): string {
	return (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

function render(patch: Partial<EditorMenuContext> = {}, props: Record<string, unknown> = {}) {
	const log: string[] = [];
	const view = mountComponent<Record<string, unknown>>(MenuFrameHost, { menu: menuContext(log, patch), log: (entry: string) => log.push(entry), ...props });
	const q = <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel);
	const tab = (id: string) => q<HTMLButtonElement>(`[data-menu-tab="${id}"]`)!;
	return { log, view, q, tab };
}

async function settle() {
	flushSync();
	await tick();
	await tick();
}

function key(target: Element, name: string, init: KeyboardEventInit = {}) {
	target.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true, ...init }));
}

function hover(el: Element) {
	el.dispatchEvent(new PointerEvent('pointerenter'));
}

beforeEach(() => {
	ensureCoreImageEditor();
	ensureCoreEditorMenu();
});

afterEach(() => {
	vi.useRealTimers();
	document.body.innerHTML = '';
});

describe('the title block and the panel', () => {
	it('is exactly as wide as the rail plus the panel, from the same values the frame uses', () => {
		const { q } = render();
		const title = q<HTMLElement>('[data-title-block]')!;
		const rail = q<HTMLElement>('[data-tool-rail]')!;
		const panel = q<HTMLElement>('[data-frame-panel]')!;
		expect(title.style.width).toBe(`${parseFloat(rail.style.width) + parseFloat(panel.style.width)}px`);
		expect(title.style.width).toBe(`${RAIL_ICON_WIDTH + settingsPaneWidth(get(viewportWidth))}px`);
	});

	it('names the tool once, truncating with an ellipsis, on the same surface as the panel', () => {
		const { q } = render({}, { title: 'Edit a region with a very long name that cannot fit' });
		const name = q('[data-title-name]')!;
		expect(name.className).toContain('truncate');
		expect(name.textContent).toContain('Edit a region');
		const title = q('[data-title-block]')!;
		expect(title.className).toContain('bg-surface-1');
		expect(q('[data-frame-panel]')!.className).toContain('bg-surface-1');
		expect(title.className).toContain('overflow-hidden');
	});

	it('has no header row in the panel: no name, no Properties, no collapse', () => {
		const { q } = render();
		const panel = q('[data-frame-panel]')!;
		expect(panel.querySelector('h2')).toBeNull();
		expect(panel.querySelector('[data-options-subtitle]')).toBeNull();
		expect(panel.querySelector('[data-options-collapse]')).toBeNull();
		expect(panel.querySelector('[data-options-panel]')!.firstElementChild!.hasAttribute('data-options-body')).toBe(true);
	});

	it('puts Apply and Reset in a footer at the bottom of the panel', () => {
		const { q, log } = render();
		const footer = q('[data-options-footer]')!;
		expect(footer.previousElementSibling!.hasAttribute('data-options-body')).toBe(true);
		q<HTMLButtonElement>('[data-options-apply]')!.click();
		q<HTMLButtonElement>('[data-options-reset]')!.click();
		expect(log).toEqual(['apply', 'reset']);
	});
});

describe('the status line', () => {
	it('shows the size and the extra part in one quiet mono line at the bottom left of the canvas', () => {
		const { q } = render();
		const status = q('[data-canvas-status]')!;
		expect(text(status)).toBe('1536×1024 · Output 1200×800');
		expect(status.className).toContain('font-mono');
		expect(status.className).toContain('tabular-nums');
		expect(status.className).toContain('left-3.5');
		expect(status.className).toContain('pointer-events-none');
		expect(q('[data-status-extra]')).not.toBeNull();
	});

	it('shows only the size when there is no extra part, and nothing without a size', () => {
		expect(text(render({}, { statusExtra: null }).q('[data-canvas-status]'))).toBe('1536×1024');
		expect(render({}, { statusSize: null }).q('[data-canvas-status]')).toBeNull();
	});
});

describe('the menu bar', () => {
	it('lists File, Edit, View and Tools as plain labels with menubar semantics', () => {
		const { q } = render();
		const bar = q('[data-editor-menubar]')!;
		expect(bar.getAttribute('role')).toBe('menubar');
		const tabs = Array.from(bar.querySelectorAll<HTMLElement>('[data-menu-tab]'));
		expect(tabs.map((tab) => text(tab))).toEqual(['File', 'Edit', 'View', 'Tools']);
		for (const tab of tabs) {
			expect(tab.getAttribute('role')).toBe('menuitem');
			expect(tab.getAttribute('aria-haspopup')).toBe('menu');
			expect(tab.getAttribute('aria-expanded')).toBe('false');
			expect(tab.className).toContain('border-transparent');
		}
	});

	it('opens a menu on click, shares the label surface and squares the top-left corner', async () => {
		const { tab } = render();
		tab('file').click();
		await settle();
		const menu = document.querySelector<HTMLElement>('[data-editor-menu="file"]')!;
		expect(menu.getAttribute('role')).toBe('menu');
		expect(tab('file').getAttribute('aria-expanded')).toBe('true');
		expect(tab('file').className).toContain('aria-expanded:bg-surface-2');
		expect(menu.className).toContain('bg-surface-2');
		expect(menu.className).toContain('rounded-tl-none');
		expect(document.querySelector('[data-editor-menu-wrap]')!.parentElement).toBe(document.body);
	});

	it('closes on a second click and on a click outside', async () => {
		const { tab } = render();
		tab('file').click();
		await settle();
		tab('file').click();
		await settle();
		expect(document.querySelector('[data-editor-menu]')).toBeNull();
		tab('edit').click();
		await settle();
		expect(document.querySelector('[data-editor-menu]')).not.toBeNull();
		document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
		await settle();
		expect(document.querySelector('[data-editor-menu]')).toBeNull();
		expect(tab('edit').getAttribute('aria-expanded')).toBe('false');
	});

	it('switches to another label on hover while one is open, and only then', async () => {
		const { tab } = render();
		hover(tab('edit'));
		await settle();
		expect(document.querySelector('[data-editor-menu]')).toBeNull();
		tab('file').click();
		await settle();
		hover(tab('view'));
		await settle();
		expect(document.querySelector('[data-editor-menu]')!.getAttribute('data-editor-menu')).toBe('view');
		expect(tab('view').getAttribute('aria-expanded')).toBe('true');
		expect(tab('file').getAttribute('aria-expanded')).toBe('false');
	});

	it('opens on ArrowDown, Enter and Space from a label and focuses the first row, or the last on ArrowUp', async () => {
		const { tab } = render();
		tab('file').focus();
		key(tab('file'), 'ArrowDown');
		await settle();
		expect(document.activeElement).toBe(menuRow('file.replace'));
		key(document.activeElement!, 'Escape');
		document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
		await settle();
		tab('edit').focus();
		key(tab('edit'), 'Enter');
		await settle();
		expect(document.querySelector('[data-editor-menu="edit"]')).not.toBeNull();
		document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
		await settle();
		tab('view').focus();
		key(tab('view'), 'ArrowUp');
		await settle();
		expect(document.activeElement).toBe(menuRow('view.edits'));
	});

	it('moves between rows with the arrows, wrapping, and Home and End', async () => {
		const { tab } = render();
		tab('edit').focus();
		key(tab('edit'), 'ArrowDown');
		await settle();
		const first = document.activeElement as HTMLElement;
		expect(first.dataset.rowId).toBe('edit.undo');
		key(first, 'ArrowDown');
		expect((document.activeElement as HTMLElement).dataset.rowId).toBe('edit.redo');
		key(document.activeElement!, 'ArrowUp');
		key(document.activeElement!, 'ArrowUp');
		expect((document.activeElement as HTMLElement).dataset.rowId).toBe('edit.remove-all');
		key(document.activeElement!, 'Home');
		expect((document.activeElement as HTMLElement).dataset.rowId).toBe('edit.undo');
		key(document.activeElement!, 'End');
		expect((document.activeElement as HTMLElement).dataset.rowId).toBe('edit.remove-all');
	});

	it('switches menus with ArrowLeft and ArrowRight from a row', async () => {
		const { tab } = render();
		tab('file').focus();
		key(tab('file'), 'ArrowDown');
		await settle();
		key(document.activeElement!, 'ArrowDown');
		key(document.activeElement!, 'ArrowDown');
		expect((document.activeElement as HTMLElement).dataset.rowId).toBe('file.save-new');
		key(document.activeElement!, 'ArrowRight');
		await settle();
		expect(document.querySelector('[data-editor-menu]')!.getAttribute('data-editor-menu')).toBe('edit');
		expect((document.activeElement as HTMLElement).dataset.rowId).toBe('edit.undo');
		key(document.activeElement!, 'ArrowRight');
		await settle();
		expect(document.querySelector('[data-editor-menu]')!.getAttribute('data-editor-menu')).toBe('view');
		key(document.activeElement!, 'ArrowLeft');
		await settle();
		expect(document.querySelector('[data-editor-menu]')!.getAttribute('data-editor-menu')).toBe('edit');
	});

	it('opens a submenu with ArrowRight, closes it with ArrowLeft and Escape, and Escape then closes the menu and returns focus to the label', async () => {
		const { tab } = render();
		tab('file').focus();
		key(tab('file'), 'ArrowDown');
		await settle();
		expect(menuRow('file.replace')!.getAttribute('aria-haspopup')).toBe('menu');
		key(document.activeElement!, 'ArrowRight');
		await settle();
		const sub = document.querySelector<HTMLElement>('[data-editor-submenu]')!;
		expect(sub.getAttribute('data-editor-submenu')).toBe('file.replace');
		expect((document.activeElement as HTMLElement).dataset.rowId).toBe('history');
		key(document.activeElement!, 'ArrowLeft');
		await settle();
		expect(document.querySelector('[data-editor-submenu]')).toBeNull();
		expect(document.activeElement).toBe(menuRow('file.replace'));
		key(document.activeElement!, 'ArrowRight');
		await settle();
		document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
		await settle();
		expect(document.querySelector('[data-editor-submenu]')).toBeNull();
		expect(document.querySelector('[data-editor-menu]')).not.toBeNull();
		document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
		await settle();
		expect(document.querySelector('[data-editor-menu]')).toBeNull();
		expect(document.activeElement).toBe(tab('file'));
	});

	it('closes the menu first on Escape, leaving what is underneath alone', async () => {
		const outer = vi.fn();
		window.addEventListener('keydown', outer);
		const { tab } = render();
		tab('file').click();
		await settle();
		document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
		await settle();
		expect(document.querySelector('[data-editor-menu]')).toBeNull();
		expect(outer).not.toHaveBeenCalled();
		window.removeEventListener('keydown', outer);
	});

	it('opens a submenu on hover and runs its row', async () => {
		const { tab, log } = render();
		tab('file').click();
		await settle();
		hover(menuRow('file.replace')!);
		await settle();
		expect(document.querySelector('[data-editor-submenu="file.replace"]')).not.toBeNull();
		menuRow('library')!.click();
		await settle();
		expect(log).toEqual(['replace:library']);
		expect(document.querySelector('[data-editor-menu]')).toBeNull();
	});

	it('shows the keybind right-aligned in mono and the leading icon column on every row', async () => {
		const { tab } = render();
		tab('edit').click();
		await settle();
		const undo = menuRow('edit.undo')!;
		const keybind = Array.from(undo.querySelectorAll('span')).find((span) => text(span) === 'Ctrl Z')!;
		expect(keybind.className).toContain('font-mono');
		expect(keybind.className).toContain('tabular-nums');
		for (const row of document.querySelectorAll('[data-menu-row]')) expect(row.querySelector('svg')).not.toBeNull();
	});
});

describe('every core item calls its action', () => {
	const cases: Array<[string, string, string]> = [
		['file', 'file.save-new', 'save:save-new'],
		['file', 'file.download', 'save:download'],
		['file', 'file.copy', 'save:copy'],
		['file', 'file.close', 'close'],
		['edit', 'edit.undo', 'undo'],
		['edit', 'edit.apply', 'apply'],
		['edit', 'edit.reset', 'reset'],
		['edit', 'edit.hide-step', 'hide'],
		['edit', 'edit.remove-step', 'remove-step'],
		['edit', 'edit.remove-all', 'remove-all'],
		['view', 'view.compare', 'compare'],
		['view', 'view.zoom-in', 'zoom-in'],
		['view', 'view.zoom-out', 'zoom-out'],
		['view', 'view.fit', 'fit'],
		['view', 'view.actual', 'actual'],
		['view', 'view.edits', 'edits'],
		['tools', 'tool:resize', 'tool:resize']
	];

	for (const [menu, row, expected] of cases) {
		it(`${row} runs ${expected}`, async () => {
			const { tab, log } = render();
			tab(menu).click();
			await settle();
			menuRow(row)!.click();
			await settle();
			expect(log).toEqual([expected]);
			expect(document.querySelector('[data-editor-menu]')).toBeNull();
		});
	}

	it('runs Redo and Replace original when they are available', async () => {
		const { tab, log } = render({ canRedo: true, redoLabel: 'Crop', replace: { allowed: true } });
		tab('edit').click();
		await settle();
		menuRow('edit.redo')!.click();
		await settle();
		tab('file').click();
		await settle();
		menuRow('file.replace-original')!.click();
		await settle();
		expect(log).toEqual(['redo', 'save:replace']);
	});

	it('runs a Send to target and a recent edit from their submenus', async () => {
		const { tab, log } = render();
		tab('file').click();
		await settle();
		hover(menuRow('file.send')!);
		await settle();
		menuRow('t1')!.click();
		await settle();
		tab('file').click();
		await settle();
		hover(menuRow('file.recent')!);
		await settle();
		menuRow('e1')!.click();
		await settle();
		tab('file').click();
		await settle();
		hover(menuRow('file.recent')!);
		await settle();
		menuRow('recent-all')!.click();
		await settle();
		expect(log).toEqual(['save:send:t1', 'recent:e1', 'all-edits']);
	});
});

describe('unavailable items', () => {
	it('keeps the row focusable, marks it disabled and does not run it', async () => {
		const { tab, log } = render({ canRedo: false });
		tab('edit').click();
		await settle();
		const redo = menuRow('edit.redo')!;
		expect(redo.getAttribute('aria-disabled')).toBe('true');
		expect(isRowDisabled('edit.redo')).toBe(true);
		expect(redo.hasAttribute('disabled')).toBe(false);
		expect(text(redo)).toContain('Nothing to redo');
		redo.click();
		await settle();
		expect(log).toEqual([]);
		expect(document.querySelector('[data-editor-menu]')).not.toBeNull();
	});

	it('shows the plain reason in the app tooltip on hover and on focus, never a native title', async () => {
		vi.useFakeTimers();
		const { tab } = render();
		tab('file').click();
		await vi.advanceTimersByTimeAsync(10);
		flushSync();
		const row = menuRow('file.replace-original')!;
		row.focus();
		row.parentElement!.dispatchEvent(new MouseEvent('mouseenter'));
		await vi.advanceTimersByTimeAsync(250);
		flushSync();
		expect(text(document.body)).toContain('Files from History stay as generated');
		expect(document.querySelector('[role="tooltip"]')).not.toBeNull();
		expect(document.querySelectorAll('[title]')).toHaveLength(0);
	});

	it('carries the reasons of the real states', async () => {
		const { tab } = render({ canUndo: false, step: null, editCount: 0, canCompare: false });
		tab('edit').click();
		await settle();
		expect(text(menuRow('edit.undo'))).toContain('Nothing to undo');
		expect(text(menuRow('edit.hide-step'))).toContain('Select an edit first');
		expect(text(menuRow('edit.remove-all'))).toContain('There are no edits to remove');
		tab('view').click();
		await settle();
		expect(text(menuRow('view.compare'))).toContain('Make an edit to compare');
	});
});

describe('the Tools menu', () => {
	it('lists every rail tool by name in the registry groups with dividers and marks the current one', async () => {
		const rail = railFor('image', { editor: 'image', hasSource: true });
		const { tab } = render({ rail, tool: 'crop' });
		tab('tools').click();
		await settle();
		const menu = document.querySelector('[data-editor-menu="tools"]')!;
		const rows = Array.from(menu.querySelectorAll<HTMLElement>('[data-menu-row]'));
		expect(rows.map((row) => text(row))).toEqual(rail.flatMap((group) => group.tools.map((tool) => tool.label)));
		expect(menu.querySelectorAll('[role="separator"]')).toHaveLength(rail.length - 1);
		const current = rows.filter((row) => row.getAttribute('aria-checked') === 'true');
		expect(current.map((row) => row.dataset.rowId)).toEqual(['tool:crop']);
		expect(current[0].className).toContain('text-signal');
		expect(current[0].getAttribute('role')).toBe('menuitemradio');
	});

	it('selects the tool exactly as the rail does', async () => {
		const { tab, log } = render();
		tab('tools').click();
		await settle();
		menuRow('tool:effects')!.click();
		await settle();
		expect(log).toEqual(['tool:effects']);
	});

	it('lists a plugin item after the core groups', async () => {
		const dispose = menuItems.register(
			{ id: 'acme.note', editor: 'image', menu: 'tools', group: 'acme.g', label: 'Note', icon: 'extension', run: () => undefined },
			pluginOwner('acme')
		);
		const { tab } = render();
		tab('tools').click();
		await settle();
		const rows = Array.from(document.querySelectorAll<HTMLElement>('[data-editor-menu="tools"] [data-menu-row]'));
		expect(text(rows.at(-1))).toBe('Note');
		dispose();
	});

	it('adds a plugin menu to the bar', async () => {
		const dispose = menuItems.register(
			{ id: 'acme.shelf', editor: 'image', menu: 'acme.shelf', menuLabel: 'Shelf', group: 'main', label: 'Shelf item', icon: 'extension', run: () => undefined },
			pluginOwner('acme')
		);
		const { tab } = render();
		expect(text(tab('acme.shelf'))).toBe('Shelf');
		tab('acme.shelf').click();
		await settle();
		expect(text(document.querySelector('[data-editor-menu="acme.shelf"] [data-menu-row]'))).toBe('Shelf item');
		dispose();
	});
});

describe('the phone menu sheet', () => {
	function sheet(patch: Partial<EditorMenuContext> = {}) {
		const log: string[] = [];
		const closed = vi.fn();
		const view = mountComponent<Record<string, unknown>>(EditorMenuSheet, { ctx: menuContext(log, { phone: true, ...patch }), fileName: 'harbor-dawn', onclose: closed });
		return { log, closed, view };
	}
	const section = (id: string) => document.querySelector<HTMLElement>(`[data-sheet-section="${id}"]`)!;
	const header = (id: string) => section(id).querySelector<HTMLButtonElement>('button')!;

	it('lists File, Edit, View and Tools as accordions in a dialog with the file name', () => {
		sheet();
		const dialog = document.querySelector('[role="dialog"]')!;
		expect(dialog.getAttribute('aria-label')).toBe('Menu');
		expect(text(dialog)).toContain('harbor-dawn');
		expect(Array.from(document.querySelectorAll('[data-sheet-section] > button')).map((button) => text(button))).toEqual(['File', 'Edit', 'View', 'Tools']);
		expect(header('file').getAttribute('aria-expanded')).toBe('false');
	});

	it('opens one section at a time with 44px rows, icons and no keybinds', async () => {
		sheet();
		header('file').click();
		await settle();
		expect(header('file').getAttribute('aria-expanded')).toBe('true');
		const rows = Array.from(section('file').querySelectorAll<HTMLElement>('[data-sheet-row]'));
		expect(rows.length).toBeGreaterThan(5);
		for (const row of rows) {
			expect(row.className).toContain('min-h-11');
			expect(row.querySelector('svg')).not.toBeNull();
		}
		expect(text(section('file'))).not.toContain('Ctrl S');
		header('edit').click();
		await settle();
		expect(header('file').getAttribute('aria-expanded')).toBe('false');
		expect(section('edit').querySelectorAll('[data-sheet-row]').length).toBeGreaterThan(3);
		expect(text(section('edit'))).not.toContain('Ctrl Z');
	});

	it('shows the reason as sub-text on an unavailable row and does not run it', async () => {
		const { log, closed } = sheet({ canRedo: false });
		header('edit').click();
		await settle();
		const redo = section('edit').querySelector<HTMLElement>('[data-sheet-row="edit.redo"]')!;
		expect(redo.getAttribute('aria-disabled')).toBe('true');
		expect(text(redo)).toContain('Nothing to redo');
		redo.click();
		expect(log).toEqual([]);
		expect(closed).not.toHaveBeenCalled();
	});

	it('opens a submenu inline, runs its row and closes the sheet', async () => {
		const { log, closed } = sheet();
		header('file').click();
		await settle();
		section('file').querySelector<HTMLElement>('[data-sheet-row="file.replace"]')!.click();
		await settle();
		const child = section('file').querySelector<HTMLElement>('[data-sheet-child="library"]')!;
		expect(child).not.toBeNull();
		child.click();
		expect(log).toEqual(['replace:library']);
		expect(closed).toHaveBeenCalledTimes(1);
	});

	it('runs a plain row, closes the sheet and marks the current tool in the Tools section', async () => {
		const { log, closed } = sheet();
		header('tools').click();
		await settle();
		const current = section('tools').querySelector<HTMLElement>('[data-sheet-row="tool:crop"]')!;
		expect(current.className).toContain('text-signal');
		expect(current.getAttribute('aria-checked')).toBe('true');
		section('tools').querySelector<HTMLElement>('[data-sheet-row="tool:resize"]')!.click();
		expect(log).toEqual(['tool:resize']);
		expect(closed).toHaveBeenCalledTimes(1);
	});

	it('does not offer zoom on a phone', async () => {
		sheet();
		header('view').click();
		await settle();
		expect(section('view').querySelector('[data-sheet-row="view.zoom-in"]')).toBeNull();
		expect(section('view').querySelector('[data-sheet-row="view.compare"]')).not.toBeNull();
	});
});
