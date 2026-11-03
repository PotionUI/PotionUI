import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { EMPTY_IMAGE_PLAN } from '$lib/media/editors/imageEditPlan';
import { apply, beginEdit, createStack } from '$lib/tool-tabs/frame/editStack';
import { emptyImageState, writeImageState } from '$lib/tool-tabs/image/state';
import { cropDraft, lookDraft } from '$lib/tool-tabs/image/steps';
import type { ToolTabActions, ToolTabHost } from '$lib/tool-tabs/types';
import type { MediaRef } from '$lib/types/tabs';

const { default: ImageEditorTab } = await import('$lib/components/tool-tabs/image/ImageEditorTab.svelte');

const HISTORY_SOURCE: MediaRef = {
	path: 'outputs/2026-11-01/gen-1/observatory-dusk.png',
	relative_path: 'outputs/2026-11-01/gen-1/observatory-dusk.png',
	url: '/api/media/generations/gen-1/observatory-dusk.png',
	name: 'observatory-dusk.png',
	type: 'image',
	generation_file_id: '501'
};

const LIBRARY_SOURCE: MediaRef = { ...HISTORY_SOURCE, generation_file_id: undefined, item_id: 'lib-1', path: 'uploads/dusk.png', relative_path: 'uploads/dusk.png' };

function twoSteps() {
	let stack = apply(
		beginEdit(
			createStack(),
			cropDraft({ plan: { ...EMPTY_IMAGE_PLAN, rect: { x: 0.125, y: 0, width: 0.75, height: 1 } }, aspect: '1:1' }, '1:1', { width: 480, height: 480 })
		)
	);
	stack = apply(beginEdit(stack, lookDraft({ kind: 'look', filter: 'reel', name: 'Reel', intensity: 60 })));
	return writeImageState({ ...emptyImageState('effects'), stack });
}

function render(overrides: Partial<ToolTabHost> = {}) {
	const calls = {
		state: [] as unknown[],
		dirty: [] as number[],
		title: [] as string[],
		tool: [] as Array<string | null>,
		actions: [] as Array<ToolTabActions | null>
	};
	const host: ToolTabHost = {
		tabId: 'tab-1',
		tool: 'image',
		source: null,
		state: null,
		activeTool: null,
		run: null,
		hint: null,
		phone: false,
		setState: (next) => calls.state.push(next),
		setDirty: (count) => calls.dirty.push(count),
		setTitle: (title) => calls.title.push(title),
		setThumbnail: () => undefined,
		setSource: () => undefined,
		replaceSource: () => undefined,
		setActiveTool: (tool) => calls.tool.push(tool),
		setRun: () => undefined,
		clearHint: () => undefined,
		setActions: (actions) => calls.actions.push(actions),
		openToolTab: () => 'tab-2',
		...overrides
	};
	const view = mountComponent(ImageEditorTab, { host });
	const q = <T extends Element = HTMLElement>(selector: string) => view.target.querySelector<T>(selector);
	const all = (selector: string) => Array.from(view.target.querySelectorAll<HTMLElement>(selector));
	const lastActions = () => calls.actions[calls.actions.length - 1];
	return { view, calls, q, all, lastActions };
}

async function settle() {
	for (let i = 0; i < 6; i++) await Promise.resolve();
	flushSync();
}

function rows(all: (selector: string) => HTMLElement[]) {
	return all('[data-edit-stack] [data-stack-row]').map((row) => `${row.dataset.status}${row.hasAttribute('data-hidden') ? ':hidden' : ''}`);
}

describe('ImageEditorTab', () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('shows the empty state with the rail greyed and nothing to save', () => {
		const { q, all, lastActions, calls } = render();
		expect(q('[data-testid="tool-tab-empty"]')?.textContent).toContain('Open a picture to edit');
		expect(q('[data-frame-file]')?.textContent).toBe('No picture');
		expect(all('[data-tool-rail] button[data-tool]').every((button) => button.getAttribute('aria-disabled') === 'true')).toBe(true);
		expect(q<HTMLButtonElement>('[data-frame-save]')?.disabled).toBe(true);
		expect(lastActions()?.save?.disabled).toBe(true);
		expect(lastActions()?.undo?.disabled).toBe(true);
		expect(lastActions()?.undo?.label).toBe('Undo');
		expect(calls.dirty).toEqual([]);
		expect(calls.state).toEqual([]);
	});

	it('restores the stack from the tab state and opens the tool the host asks for', () => {
		const { q, all, calls } = render({ source: HISTORY_SOURCE, state: twoSteps(), activeTool: 'effects' });
		expect(rows(all)).toEqual(['original', 'applied', 'current']);
		expect(q('[data-image-editor]')?.dataset.tool).toBe('effects');
		expect(q('[data-options-panel] h2')?.textContent).toBe('Effects');
		expect(q('[data-effects-tab="looks"]')).not.toBeNull();
		expect(calls.title).toContain('observatory-dusk');
		expect(calls.dirty).toEqual([2]);
	});

	it('moves the cursor on undo and redo and persists the change through the host', () => {
		const { q, all, calls, lastActions } = render({ source: HISTORY_SOURCE, state: twoSteps(), activeTool: 'crop' });
		q<HTMLButtonElement>('[data-frame-undo]')!.click();
		flushSync();
		expect(rows(all)).toEqual(['original', 'current', 'undone']);
		expect(lastActions()?.redo?.label).toBe('Redo Reel');
		const written = calls.state[calls.state.length - 1] as { stack: { cursor: number } };
		expect(written.stack.cursor).toBe(1);
		expect(calls.dirty[calls.dirty.length - 1]).toBe(1);
		q<HTMLButtonElement>('[data-frame-redo]')!.click();
		flushSync();
		expect(rows(all)).toEqual(['original', 'applied', 'current']);
	});

	it('hides a step from the row action and keeps it in the stack', () => {
		const { q, all } = render({ source: HISTORY_SOURCE, state: twoSteps(), activeTool: 'crop' });
		q<HTMLButtonElement>('[data-stack-hide="s1"]')!.click();
		flushSync();
		expect(rows(all)).toEqual(['original', 'applied:hidden', 'current']);
	});

	it('disables Replace original for a History picture with its reason, and allows it for a Library one', async () => {
		const history = render({ source: HISTORY_SOURCE, state: twoSteps(), activeTool: 'crop' });
		history.q<HTMLButtonElement>('[data-frame-save-menu]')!.click();
		flushSync();
		const replace = document.querySelector<HTMLButtonElement>('[data-save-row="replace"]')!;
		expect(replace.disabled).toBe(true);
		expect(replace.textContent).toContain('Files from History stay as generated');
		history.view.destroy();

		const library = render({ source: LIBRARY_SOURCE, state: twoSteps(), activeTool: 'crop' });
		library.q<HTMLButtonElement>('[data-frame-save-menu]')!.click();
		flushSync();
		const allowed = document.querySelector<HTMLButtonElement>('[data-save-row="replace"]')!;
		expect(allowed.disabled).toBe(false);
		expect(allowed.textContent).toContain('Overwrites the Library item');
	});

	it('lays out the phone frame with the tool strip and fills the top bar actions', () => {
		const { q, lastActions } = render({ source: HISTORY_SOURCE, state: twoSteps(), activeTool: 'effects', phone: true });
		expect(q('[data-editor-frame="phone"]')).not.toBeNull();
		expect(q('[data-tool-rail="strip"]')).not.toBeNull();
		expect(q('[data-options-panel="phone"]')).not.toBeNull();
		expect(q('[data-editor-toolbar]')).toBeNull();
		expect(q('[data-frame-edits]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('2 edits');
		expect(lastActions()?.undo?.label).toBe('Undo Reel');
		expect(lastActions()?.save?.label).toBe('Save');
		expect(lastActions()?.save?.disabled).toBe(false);
	});

	it('switching tools applies a pending edit as an undoable step', async () => {
		const state = twoSteps() as { stack: unknown };
		const pending = { ...state, stack: { ...(state.stack as object), pending: { ...lookDraft({ kind: 'look', filter: 'noir', name: 'Noir', intensity: 40 }), target: null } } };
		const { q, all, calls, lastActions } = render({ source: HISTORY_SOURCE, state: pending, activeTool: 'effects' });
		expect(rows(all)).toEqual(['original', 'applied', 'applied', 'pending']);
		expect(lastActions()?.undo?.label).toBe('Undo Noir');
		q<HTMLButtonElement>('[data-tool-rail] button[data-tool="resize"]')!.click();
		await settle();
		expect(q('[data-image-editor]')?.dataset.tool).toBe('resize');
		expect(calls.tool).toContain('resize');
		expect(rows(all)).toEqual(['original', 'applied', 'applied', 'current']);
		expect(all('[data-stack-row]').at(-1)?.textContent).toContain('Noir');
	});
});
