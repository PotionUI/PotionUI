import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, tick } from 'svelte';
import { mountComponent } from '../helpers/mount';
import type { SaveAction } from '$lib/tool-tabs/frame/types';

const { default: EditorToolbar } = await import('$lib/components/tool-tabs/frame/EditorToolbar.svelte');

const targets = [
	{ id: 'tab-1', name: 'Portrait study', detail: 'Krea 2 · Reference image', color: 'rgb(144 133 233)' },
	{ id: 'tab-2', name: 'Inpaint', detail: 'Qwen Edit · Image + mask', badge: 'MASK' }
];

function render(props: Record<string, unknown> = {}) {
	const actions: SaveAction[] = [];
	const calls: string[] = [];
	const view = mountComponent<Record<string, unknown>>(EditorToolbar, {
		fileName: 'observatory-dusk.png',
		chips: ['1024×1024'],
		source: { icon: 'clock', label: 'History · Krea 2' },
		replace: { allowed: false, reason: 'Files from History stay as generated' },
		sendTargets: targets,
		newGeneration: true,
		downloadFormat: 'PNG',
		onundo: () => calls.push('undo'),
		onredo: () => calls.push('redo'),
		oncompare: (next: boolean) => calls.push(`compare:${next}`),
		onsave: (action: SaveAction) => actions.push(action),
		oncancelsave: () => calls.push('cancel'),
		...props
	});
	const q = <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel);
	return { actions, calls, view, q };
}

function text(el: Element | null | undefined): string {
	return (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

async function openMenu(q: (sel: string) => HTMLElement | null) {
	q('[data-frame-save-menu]')!.click();
	flushSync();
	await tick();
	return document.querySelector<HTMLElement>('[role="menu"]')!;
}

afterEach(() => {
	vi.useRealTimers();
	document.body.innerHTML = '';
});

describe('EditorToolbar file and history', () => {
	it('shows the file name, chips and source', () => {
		const { q } = render();
		expect(text(q('[data-frame-file]'))).toBe('observatory-dusk.png');
		expect(text(q('[data-editor-toolbar]'))).toContain('1024×1024');
		expect(text(q('[data-editor-toolbar]'))).toContain('History · Krea 2');
	});

	it('shows the empty label and no chips without a file', () => {
		const { q } = render({ fileName: null });
		expect(text(q('[data-frame-file]'))).toBe('No picture');
		expect(text(q('[data-editor-toolbar]'))).not.toContain('1024×1024');
	});

	it('enables undo and redo from props and names the step in the label', () => {
		const { q, calls } = render({ canUndo: true, canRedo: false, undoLabel: 'Reel' });
		const undo = q<HTMLButtonElement>('[data-frame-undo]')!;
		const redo = q<HTMLButtonElement>('[data-frame-redo]')!;
		expect(undo.disabled).toBe(false);
		expect(redo.disabled).toBe(true);
		expect(undo.getAttribute('aria-label')).toBe('Undo Reel');
		undo.click();
		expect(calls).toEqual(['undo']);
	});

	it('shows the undo tooltip with its shortcut and never a native title', async () => {
		vi.useFakeTimers();
		const { q } = render({ canUndo: true, undoLabel: 'Reel' });
		const undo = q<HTMLButtonElement>('[data-frame-undo]')!;
		undo.parentElement!.dispatchEvent(new MouseEvent('mouseenter'));
		await vi.advanceTimersByTimeAsync(250);
		flushSync();
		expect(text(document.body)).toContain('Undo Reel');
		expect(text(document.body)).toContain('Ctrl Z');
		expect(document.querySelectorAll('[title]')).toHaveLength(0);
	});

	it('toggles before/after and shows it pressed in signal', () => {
		const { q, calls, view } = render();
		const button = q<HTMLButtonElement>('[data-frame-compare]')!;
		expect(button.getAttribute('aria-pressed')).toBe('false');
		button.click();
		expect(calls).toEqual(['compare:true']);
		view.rerender({ compare: true });
		expect(button.getAttribute('aria-pressed')).toBe('true');
		expect(button.className).toContain('text-signal');
		expect(text(button)).toContain('\\');
	});
});

describe('EditorToolbar save', () => {
	it('saves as new before the first save and saves the copy after it', () => {
		const { q, actions, view } = render();
		const primary = q<HTMLButtonElement>('[data-frame-save]')!;
		expect(text(primary)).toBe('Save as new');
		primary.click();
		view.rerender({ saved: true });
		expect(text(primary)).toBe('Save');
		primary.click();
		expect(actions).toEqual([{ kind: 'save-new' }, { kind: 'save' }]);
	});

	it('lists save, replace with its reason, send targets, download and copy', async () => {
		const { q } = render();
		const menu = await openMenu(q);
		const rows = Array.from(menu.querySelectorAll<HTMLButtonElement>('[data-save-row]')).map((row) => row.dataset.saveRow);
		expect(rows).toEqual(['save-new', 'replace', 'send', 'send', 'new-generation', 'download', 'copy']);
		expect(text(menu.querySelector('[data-save-row="save-new"]'))).toContain('Ctrl S');
		const replace = menu.querySelector<HTMLButtonElement>('[data-save-row="replace"]')!;
		expect(replace.disabled).toBe(true);
		expect(text(replace)).toContain('Files from History stay as generated');
		expect(text(menu)).toContain('Send to a generation');
		expect(text(menu)).toContain('MASK');
		expect(text(menu.querySelector('[data-save-row="download"]'))).toContain('PNG');
	});

	it('offers Save and Save as new after the first save, with Ctrl S on Save', async () => {
		const { q } = render({ saved: true, replace: { allowed: true } });
		const menu = await openMenu(q);
		expect(text(menu.querySelector('[data-save-row="save"]'))).toContain('Ctrl S');
		expect(text(menu.querySelector('[data-save-row="save-new"]'))).not.toContain('Ctrl S');
		expect(menu.querySelector<HTMLButtonElement>('[data-save-row="replace"]')!.disabled).toBe(false);
	});

	it('hides the send group when there is nowhere to send', async () => {
		const { q } = render({ sendTargets: [], newGeneration: false });
		const menu = await openMenu(q);
		expect(text(menu)).not.toContain('Send to a generation');
	});

	it('reports the chosen row and closes the menu', async () => {
		const { q, actions } = render();
		const menu = await openMenu(q);
		menu.querySelectorAll<HTMLButtonElement>('[data-save-row="send"]')[1].click();
		flushSync();
		expect(actions).toEqual([{ kind: 'send', target: 'tab-2' }]);
		expect(document.querySelector('[role="menu"]')).toBeNull();
	});

	it('swaps the save button for a tick-ruler progress, percentage and Cancel while saving', () => {
		const { q, calls } = render({ saving: { percent: 41.6 } });
		expect(q('[data-frame-save]')).toBeNull();
		const bar = q('[role="progressbar"]')!;
		expect(bar.getAttribute('aria-valuenow')).toBe('42');
		expect(text(q('[data-frame-saving]'))).toBe('42%');
		q<HTMLButtonElement>('[data-frame-cancel-save]')!.click();
		expect(calls).toEqual(['cancel']);
	});

	it('disables save with nothing loaded', () => {
		const { q } = render({ canSave: false, canCompare: false });
		expect(q<HTMLButtonElement>('[data-frame-save]')!.disabled).toBe(true);
		expect(q<HTMLButtonElement>('[data-frame-save-menu]')!.disabled).toBe(true);
		expect(q<HTMLButtonElement>('[data-frame-compare]')!.disabled).toBe(true);
	});
});

describe('EditorToolbar on a phone', () => {
	it('opens the save options as a sheet and reports the chosen row', () => {
		const { q, actions } = render({ phone: true });
		expect(q('[data-editor-toolbar="phone"]')).not.toBeNull();
		q<HTMLButtonElement>('[data-frame-save]')!.click();
		flushSync();
		const sheet = document.querySelector('[data-frame-sheet] [role="dialog"]')!;
		expect(sheet.getAttribute('aria-label')).toBe('Save');
		expect(text(sheet)).toContain('observatory-dusk.png');
		sheet.querySelector<HTMLButtonElement>('[data-save-row="download"]')!.click();
		flushSync();
		expect(actions).toEqual([{ kind: 'download' }]);
		expect(document.querySelector('[data-frame-sheet]')).toBeNull();
	});

	it('shows the save percentage in place of Save while saving', () => {
		const { q } = render({ phone: true, saving: { percent: 42 } });
		expect(q('[data-frame-save]')).toBeNull();
		expect(text(q('[data-frame-saving]'))).toBe('42%');
	});
});

describe('EditorToolbar more menu', () => {
	it('hides the more menu without items', () => {
		const { q } = render();
		expect(q('[aria-label="More"]')).toBeNull();
	});

	it('lists the extra items and reports the chosen one', async () => {
		const picked: string[] = [];
		const { q } = render({ moreItems: [{ id: 'reveal', label: 'Show source' }], onmore: (id: string) => picked.push(id) });
		q<HTMLButtonElement>('[aria-label="More"]')!.click();
		flushSync();
		await tick();
		const item = document.querySelector<HTMLButtonElement>('[role="menu"] [role="menuitem"]')!;
		expect(text(item)).toBe('Show source');
		item.click();
		flushSync();
		expect(picked).toEqual(['reveal']);
	});
});
