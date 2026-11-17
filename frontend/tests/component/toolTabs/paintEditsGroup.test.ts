import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync } from 'svelte';
import { installFakeCanvas } from '../helpers/fakeCanvas';
import { mountComponent } from '../helpers/mount';
import { resetRecentColours } from '$lib/paint/recentColours';
import { apply, beginEdit, createStack, reopen, type EditStackState, type StepDraft } from '$lib/tool-tabs/frame/editStack';

vi.mock('$lib/services/api/index', () => ({
	api: { listFilters: async () => ({ filters: [], ops: [], groups: [] }) }
}));

const { default: Host } = await import('./PaintGroupHost.svelte');
const { default: EditStack } = await import('$lib/components/tool-tabs/frame/EditStack.svelte');
const { default: PaintPanel } = await import('$lib/components/paint/PaintPanel.svelte');
const { default: PaintOptions } = await import('$lib/components/tool-tabs/image/PaintOptions.svelte');
const { PaintSession } = await import('$lib/paint/session');

const crop: StepDraft = { tool: 'crop', icon: 'editor-crop', label: 'Crop', summary: '4:3', params: {}, lane: 'geometry' };
const resize: StepDraft = { tool: 'resize', icon: 'editor-resize', label: 'Resize', summary: '1536×1152', params: {}, lane: 'geometry' };
const paint: StepDraft = { tool: 'paint', icon: 'editor-paint', label: 'Paint', summary: '2 layers', params: {}, lane: 'paint' };

function build(...drafts: StepDraft[]): EditStackState {
	return drafts.reduce((state, draft) => apply(beginEdit(state, draft)), createStack());
}

let restore: () => void;
let session: InstanceType<typeof PaintSession>;
let scrolled: ReturnType<typeof vi.fn>;

const q = (selector: string) => document.body.querySelector<HTMLElement>(selector);
const all = (selector: string) => Array.from(document.body.querySelectorAll<HTMLElement>(selector));
const text = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();

function mountGroup(stack: EditStackState, extra: Record<string, unknown> = {}) {
	const view = mountComponent(Host as never, { stack, session, state: session.snapshot(), progress: { target: null }, ...extra });
	session.subscribe(() => view.rerender({ state: session.snapshot() }));
	flushSync();
	return view;
}

async function choose(index: number, label: string) {
	q(`[data-stack-group] [data-layer-row="${index}"] button[aria-label^="Actions for"]`)!.click();
	flushSync();
	await Promise.resolve();
	all('[data-layer-menu] [role="menuitem"]').find((node) => node.textContent?.trim() === label)!.click();
	flushSync();
	await Promise.resolve();
}

function float() {
	session.selectAll();
	session.cutSelection();
	session.pasteClipboard();
	flushSync();
}

beforeEach(() => {
	restore = installFakeCanvas();
	resetRecentColours();
	scrolled = vi.fn();
	Element.prototype.scrollIntoView = scrolled as never;
	session = new PaintSession();
	session.openBlank({ width: 32, height: 32, background: 'white', pen: null });
});

afterEach(() => {
	session.detach();
	document.body.innerHTML = '';
	restore();
});

describe('the in-progress Paint group in the Edits list', () => {
	it('appears after the applied steps as one open group with an in-progress state', () => {
		mountGroup(build(crop, resize));
		const group = q('[data-stack-group]')!;
		expect(group).not.toBeNull();
		expect(text(q('[data-stack-group-header]'))).toContain('Paint');
		expect(text(q('[data-stack-state="in-progress"]'))).toContain('In progress');
		const order = all('[data-stack-row], [data-stack-group]').map((node) => node.dataset.stackRow ?? 'group');
		expect(order).toEqual(['original', 's1', 's2', 'group']);
		expect(all('[data-stack-group] [data-layer-row]').map((row) => row.dataset.layerRow)).toEqual(['0']);
	});

	it('shows with one empty layer and counts layers apart from steps', () => {
		mountGroup(build(crop));
		expect(text(q('[data-layer-count]'))).toBe('1');
		expect(text(q('[data-stack-count]'))).toBe('1');
		session.addLayer();
		session.addLayer();
		flushSync();
		expect(text(q('[data-layer-count]'))).toBe('3');
		expect(all('[data-stack-group] [data-layer-row]')).toHaveLength(3);
		expect(text(q('[data-stack-count]'))).toBe('1');
		expect(all('[data-stack-row]').map((row) => row.dataset.stackRow)).toEqual(['original', 's1']);
	});

	it('renders the same layer rows, nested under a rule, with a smaller thumbnail than the modal list', () => {
		session.addLayer();
		mountGroup(build(crop));
		const rows = all('[data-stack-group] [data-layer-row]');
		expect(rows.map((row) => row.dataset.layerRow)).toEqual(['1', '0']);
		expect(q('[data-paint-layers-nested]')).not.toBeNull();
		expect(rows[0].className).toContain('before:');
		expect(q('[data-stack-group] [data-layer-row] canvas')!.getAttribute('class')).toContain('h-7');
		expect(q('[data-layers-note]')!.textContent).toContain('one edit');
	});

	it('selects a layer without touching the tool and toggles visibility', () => {
		session.addLayer();
		mountGroup(build(crop));
		const tool = session.snapshot().toolId;
		q('[data-layer-row="0"] [data-layer-select]')!.click();
		expect(session.snapshot().activeIndex).toBe(0);
		expect(session.snapshot().toolId).toBe(tool);
		q('[data-layer-row="0"] [data-layer-eye]')!.click();
		expect(session.snapshot().layers[0].visible).toBe(false);
	});

	it('adds a layer from the plus on the group header', () => {
		mountGroup(build(crop));
		q('[data-stack-group-header] button[aria-label="Add a layer"]')!.click();
		flushSync();
		all('[role="menuitem"]').find((node) => node.textContent?.includes('New layer'))!.click();
		flushSync();
		expect(session.snapshot().layers).toHaveLength(2);
	});

	it('shows the floating piece as a marked row with Place, and places it', () => {
		mountGroup(build(crop));
		float();
		const row = q('[data-stack-group] [data-floating-row]')!;
		expect(row).not.toBeNull();
		expect(text(row)).toContain('Floating piece');
		expect(text(row)).toContain('Enter');
		q('[data-floating-place]')!.click();
		flushSync();
		expect(q('[data-floating-row]')).toBeNull();
		expect(session.snapshot().floatingId).toBeNull();
	});

	it('scrolls the floating row into view once, not on every move', () => {
		mountGroup(build(crop));
		expect(scrolled).not.toHaveBeenCalled();
		float();
		expect(scrolled).toHaveBeenCalledTimes(1);
		session.nudge(2, 2);
		session.setActiveLayer(0);
		flushSync();
		expect(scrolled).toHaveBeenCalledTimes(1);
	});

	it('runs rename, duplicate, merge down, move and delete from the row menu', async () => {
		session.addLayer();
		mountGroup(build(crop));
		await choose(1, 'Duplicate');
		expect(session.snapshot().layers).toHaveLength(3);
		await choose(2, 'Merge down');
		expect(session.snapshot().layers).toHaveLength(2);
		await choose(1, 'Move down');
		await choose(0, 'Delete');
		expect(session.snapshot().layers).toHaveLength(1);
		expect(text(q('[data-layer-count]'))).toBe('1');
	});

	it('reorders layers by drag within the group and offers no grip into or out of it', () => {
		session.addLayer();
		session.renameLayer(1, 'Top');
		mountGroup(build(crop));
		const handle = q('[data-stack-group] [data-layer-row="1"] [data-layer-handle]')!;
		const target = q('[data-stack-group] [data-layer-row="0"]')!;
		target.getBoundingClientRect = () => ({ top: 0, height: 40, left: 0, right: 100, bottom: 40, width: 100, x: 0, y: 0, toJSON() {} }) as DOMRect;
		handle.dispatchEvent(new Event('dragstart', { bubbles: true }));
		const over = new Event('dragover', { bubbles: true, cancelable: true }) as Event & { clientY: number };
		over.clientY = 35;
		target.dispatchEvent(over);
		target.dispatchEvent(new Event('drop', { bubbles: true, cancelable: true }));
		expect(session.snapshot().layers.map((layer) => layer.name)).toEqual(['Top', 'Background']);
		expect(all('[data-stack-group] [data-stack-grip]')).toHaveLength(0);
		expect(all('[data-stack-row] [data-layer-row]')).toHaveLength(0);
		expect(all('[data-stack-grip]').length).toBeGreaterThan(0);
	});

	it('closes into the one step row when the session ends', () => {
		const view = mountGroup(build(crop));
		expect(q('[data-stack-group]')).not.toBeNull();
		view.rerender({ stack: build(crop, paint), progress: null } as never);
		expect(q('[data-stack-group]')).toBeNull();
		expect(all('[data-stack-row]').map((row) => row.dataset.stackRow)).toEqual(['original', 's1', 's2']);
		expect(text(q('[data-stack-count]'))).toBe('2');
	});

	it('sits at the step position when an applied paint step is reopened', () => {
		const stack = reopen(build(crop, paint, resize), 's2');
		mountGroup(stack, { progress: { target: 's2' } });
		const order = all('[data-stack-row], [data-stack-group]').map((node) => (node.dataset.stackGroup !== undefined ? 'group' : node.dataset.stackRow));
		expect(order).toEqual(['original', 's1', 's2', 'group', 's3']);
		const hidden = q('[data-stack-row="s2"]')!;
		expect(hidden.className).toContain('!hidden');
		expect(text(q('[data-stack-group-header]')).startsWith('02')).toBe(true);
		expect(text(q('[data-stack-count]'))).toBe('3');
	});

	it('keeps layer rows in the Edits sheet on a phone', () => {
		session.addLayer();
		mountGroup(build(crop), { variant: 'sheet', phone: true });
		expect(q('[data-edit-stack="sheet"] [data-stack-group]')).not.toBeNull();
		expect(all('[data-stack-group] [data-layer-row]')).toHaveLength(2);
		expect(q('[data-stack-group] [data-layer-row] > div > div')!.className).toContain('min-h-[52px]');
		expect(text(q('[data-stack-count]'))).toBe('1');
	});
});

describe('the paint options without the Layers section', () => {
	it.each(['brush', 'move', 'transform'])('%s panel has no layers when the editor lists them in Edits', (tool) => {
		session.setTool(tool);
		mountComponent(PaintPanel as never, { session, state: session.snapshot(), withLayers: false });
		expect(q('[data-paint-tool-panel]')).not.toBeNull();
		expect(q('[data-paint-layers]')).toBeNull();
	});

	it('the modal panel keeps its layers by default', () => {
		session.setTool('brush');
		mountComponent(PaintPanel as never, { session, state: session.snapshot() });
		expect(q('[data-paint-layers]')).not.toBeNull();
		expect(q('[data-paint-layers-nested]')).toBeNull();
		expect(q('[data-layer-count]')).not.toBeNull();
		expect(q('button[aria-label="Add a layer"]')).not.toBeNull();
	});

	it('the phone options drop the Layers button and sheet when layers live in Edits, and keep them for the modal', () => {
		session.setTool('brush');
		mountComponent(PaintOptions as never, { session, state: session.snapshot(), phone: true, layers: false });
		expect(q('[data-paint-layers-button]')).toBeNull();
		document.body.innerHTML = '';
		mountComponent(PaintOptions as never, { session, state: session.snapshot(), phone: true });
		expect(q('[data-paint-layers-button]')).not.toBeNull();
		q('[data-paint-layers-button]')!.click();
		flushSync();
		expect(q('[data-paint-layers-sheet] [data-layer-row]')).not.toBeNull();
	});
});

describe('the generic in-progress hook of the Edits list', () => {
	const row = createRawSnippet((index: () => number) => ({ render: () => `<span data-fake-group>group at ${index()}</span>` }));

	function mountFake(stack: EditStackState, progress: { target: string | null } | null) {
		const none = () => undefined;
		return mountComponent(EditStack as never, {
			stack,
			original: { summary: 'x' },
			progress,
			progressRow: row,
			onreopen: none,
			onseek: none,
			ontogglehidden: none,
			onremove: none,
			onmove: none
		});
	}

	it('renders whatever a tool contributes at the end of the applied steps', () => {
		mountFake(build(crop, resize), { target: null });
		expect(text(q('[data-stack-group]'))).toBe('group at 3');
		expect(all('[data-stack-row]')).toHaveLength(3);
	});

	it('renders nothing without progress, and replaces the reopened step row at its position', () => {
		mountFake(build(crop), null);
		expect(q('[data-stack-group]')).toBeNull();
		document.body.innerHTML = '';
		mountFake(reopen(build(crop, resize), 's1'), { target: 's1' });
		expect(text(q('[data-stack-group]'))).toBe('group at 1');
		expect(q('[data-stack-row="s1"]')!.className).toContain('!hidden');
	});

	it('puts the group before undone steps', () => {
		const stack = { ...build(crop, resize), cursor: 1 };
		mountFake(stack, { target: null });
		const order = all('[data-stack-row], [data-stack-group]').map((node) => node.dataset.stackRow ?? 'group');
		expect(order).toEqual(['original', 's1', 'group', 's2']);
	});
});
