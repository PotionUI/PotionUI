import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { installFakeCanvas } from '../helpers/fakeCanvas';
import { mountComponent } from '../helpers/mount';
import { resetRecentColours } from '$lib/paint/recentColours';
import { apply, beginEdit, createStack, reopen, type EditStackState, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import { arrangeLayers, isLayerStep, layerThumb } from '$lib/tool-tabs/image/layers';
import type { LiveLayer } from '$lib/tool-tabs/frame/types';

vi.mock('$lib/services/api/index', () => ({
	api: { listFilters: async () => ({ filters: [], ops: [], groups: [] }) }
}));

const { default: Host } = await import('./StackFoldHost.svelte');
const { default: PaintPanel } = await import('$lib/components/paint/PaintPanel.svelte');
const { default: PaintOptions } = await import('$lib/components/tool-tabs/image/PaintOptions.svelte');
const { PaintSession } = await import('$lib/paint/session');

const sketch: StepDraft = { tool: 'paint', icon: 'editor-paint', label: 'Paint 1', summary: 'Paint layer', params: { url: 'data:image/png;base64,AA==', layers: 1, name: 'Paint 1' }, lane: 'paint' };
const second: StepDraft = { ...sketch, label: 'Paint 2', params: { url: 'data:image/png;base64,AA==', layers: 1, name: 'Paint 2' } };
const crop: StepDraft = { tool: 'crop', icon: 'editor-crop', label: 'Crop', summary: '4:3', params: {}, lane: 'geometry' };

function layered(...drafts: StepDraft[]): EditStackState {
	return drafts.reduce((state, draft) => arrangeLayers(apply(beginEdit(state, draft))), createStack());
}

let restore: () => void;
let session: InstanceType<typeof PaintSession>;

const q = (selector: string) => document.body.querySelector<HTMLElement>(selector);
const all = (selector: string) => Array.from(document.body.querySelectorAll<HTMLElement>(selector));
const order = () => all('[data-stack-section="layers"] [data-stack-row], [data-stack-section="layers"] [data-stack-live]').map((row) => row.dataset.stackRow ?? 'live');
const text = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();

function live(extra: Partial<LiveLayer> = {}): LiveLayer {
	return { label: 'Paint 3', hidden: false, opacity: 1, onhide: vi.fn(), onopacity: vi.fn(), ...extra };
}

function mountStack(stack: EditStackState, progress: Record<string, unknown> | null, extra: Record<string, unknown> = {}) {
	return mountComponent(Host as never, { stack, isLayer: isLayerStep, layerThumb, progress, ...extra });
}

beforeEach(() => {
	localStorage.clear();
	restore = installFakeCanvas();
	resetRecentColours();
	session = new PaintSession();
	session.openBlank({ width: 32, height: 32, background: 'white', pen: null });
});

afterEach(() => {
	session.detach();
	document.body.innerHTML = '';
	localStorage.clear();
	restore();
});

describe('the new paint layer in the Layers panel', () => {
	it('is one layer row above the top layer, not a group', () => {
		mountStack(layered(sketch, second, crop), { target: null, live: live() });
		expect(order()).toEqual(['live', 's2', 's1', 'original']);
		expect(q('[data-stack-group]')).toBeNull();
		expect(q('[data-paint-group]')).toBeNull();
		expect(q('[data-paint-layers]')).toBeNull();
		expect(q('[data-layer-row]')).toBeNull();
		expect(text(q('[data-stack-live-name]'))).toBe('Paint 3');
	});

	it('never lands in the Edits panel, even when the last step is an edit', () => {
		mountStack(layered(sketch, crop), { target: null, live: live() });
		expect(all('[data-stack-section="edits"] [data-stack-row]').map((row) => row.dataset.stackRow)).toEqual(['s2']);
		expect(q('[data-stack-section="edits"] [data-stack-live]')).toBeNull();
	});

	it('sits above the layer it was added on', () => {
		mountStack(layered(sketch, second), { target: null, after: 's1', live: live() });
		expect(order()).toEqual(['s2', 'live', 's1', 'original']);
	});

	it('is the selected row and does not count as a step', () => {
		mountStack(layered(sketch), { target: null, live: live() });
		expect(q('[data-stack-live]')!.getAttribute('aria-current')).toBe('true');
		expect(text(q('[data-stack-count="layers"]'))).toBe('1');
	});

	it('hides and fades the layer through the live callbacks', () => {
		const layer = live();
		mountStack(layered(sketch), { target: null, live: layer });
		q('[data-stack-hide="live"]')!.click();
		expect(layer.onhide).toHaveBeenCalledTimes(1);
		expect(text(q('[data-stack-opacity="live"]'))).toBe('100%');
		expect(q('[data-layer-drawer="live"]')).not.toBeNull();
	});

	it('shows a hidden live layer dimmed', () => {
		mountStack(layered(sketch), { target: null, live: live({ hidden: true }) });
		expect(q('[data-stack-live]')!.dataset.hidden).toBe('true');
	});

	it('shows no extra row for a reopened layer: the step row itself is selected', () => {
		mountStack(reopen(layered(sketch, second), 's2'), { target: 's2', live: live() });
		expect(q('[data-stack-live]')).toBeNull();
		expect(all('[data-stack-row]').map((row) => row.dataset.stackRow)).toEqual(['s2', 's1', 'original']);
		expect(q('[data-stack-row="s2"]')!.getAttribute('aria-current')).toBe('true');
		expect(q('[data-layer-drawer="s2"]')).not.toBeNull();
	});

	it('closes into the plain step rows when the session ends', () => {
		const view = mountStack(layered(sketch), { target: null, live: live() });
		expect(q('[data-stack-live]')).not.toBeNull();
		view.rerender({ stack: layered(sketch, second), progress: null } as never);
		expect(q('[data-stack-live]')).toBeNull();
		expect(all('[data-stack-section="layers"] [data-stack-row]').map((row) => row.dataset.stackRow)).toEqual(['s2', 's1', 'original']);
	});

	it('keeps the row in the phone sheet', () => {
		mountStack(layered(sketch), { target: null, live: live() }, { variant: 'sheet' });
		expect(q('[data-edit-stack="sheet"] [data-stack-live]')).not.toBeNull();
		expect(q('[data-stack-opacity="live"]')).toBeNull();
	});
});

describe('the paint options without a layer list', () => {
	it.each(['brush', 'move', 'transform'])('%s panel has no layers when the editor lists them', (tool) => {
		session.setTool(tool);
		mountComponent(PaintPanel as never, { session, state: session.snapshot(), withLayers: false });
		expect(q('[data-paint-tool-panel]')).not.toBeNull();
		expect(q('[data-paint-layers]')).toBeNull();
	});

	it('the standalone paint panel keeps its own layers', () => {
		session.setTool('brush');
		mountComponent(PaintPanel as never, { session, state: session.snapshot() });
		expect(q('[data-paint-layers]')).not.toBeNull();
		expect(q('button[aria-label="Add a layer"]')).not.toBeNull();
	});

	it('the phone options drop the Layers button and sheet when the editor lists them', () => {
		session.setTool('brush');
		mountComponent(PaintOptions as never, { session, state: session.snapshot(), phone: true, layers: false });
		expect(q('[data-paint-layers-button]')).toBeNull();
		document.body.innerHTML = '';
		mountComponent(PaintOptions as never, { session, state: session.snapshot(), phone: true });
		expect(q('[data-paint-layers-button]')).not.toBeNull();
	});

	it('names the layer the strokes go to', () => {
		session.setTool('brush');
		mountComponent(PaintOptions as never, { session, state: session.snapshot(), layers: false, target: { name: 'Paint 2', thumb: null } });
		expect(text(q('[data-paint-target="layer"]'))).toContain('Painting on Paint 2');
		document.body.innerHTML = '';
		mountComponent(PaintOptions as never, { session, state: session.snapshot(), layers: false, target: { name: null, thumb: null } });
		expect(text(q('[data-paint-target="new"]'))).toContain('Painting on a new layer');
	});

	it('offers Place and Keep for a floating piece when the editor lists the layers', () => {
		session.setTool('move');
		session.selectAll();
		session.cutSelection();
		session.pasteClipboard();
		flushSync();
		mountComponent(PaintOptions as never, { session, state: session.snapshot(), layers: false });
		expect(q('[data-floating-row]')).not.toBeNull();
		q('[data-floating-place]')!.click();
		flushSync();
		expect(session.floatingLayer).toBeNull();
	});
});
