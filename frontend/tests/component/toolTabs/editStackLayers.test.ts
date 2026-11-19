import { describe, expect, it } from 'vitest';
import { mountComponent } from '../helpers/mount';
import { apply, beginEdit, createStack, reopen, setOpacity, type EditStackState, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import { arrangeLayers, isLayerStep, layerThumb, patchMaskOf } from '$lib/tool-tabs/image/layers';

const { default: EditStack } = await import('$lib/components/tool-tabs/frame/EditStack.svelte');

const paint: StepDraft = { tool: 'paint', icon: 'editor-paint', label: 'Sketch', summary: 'Paint layer', params: { url: 'data:image/png;base64,AA==' }, lane: 'paint' };
const patch: StepDraft = { tool: 'generate', icon: 'editor-region', label: 'Add clouds', summary: 'Edit a region', params: {}, lane: 'paint' };
const crop: StepDraft = { tool: 'crop', icon: 'editor-crop', label: 'Crop', summary: '4:3', params: {}, lane: 'geometry' };
const warm: StepDraft = { tool: 'effects', icon: 'editor-effects', label: 'Warm light', summary: 'Intensity 80', params: {} };

function build(...drafts: StepDraft[]): EditStackState {
	return drafts.reduce((state, draft) => apply(beginEdit(state, draft)), createStack());
}

function layered(...drafts: StepDraft[]): EditStackState {
	return drafts.reduce((state, draft) => arrangeLayers(apply(beginEdit(state, draft))), createStack());
}

function render(stack: EditStackState, extra: Record<string, unknown> = {}) {
	const calls: string[] = [];
	const view = mountComponent(EditStack, {
		...extra,
		stack,
		original: { summary: '1024×1024 · PNG' },
		layered: true,
		isLayer: isLayerStep,
		layerThumb,
		onopacity: (id: string, value: number, live: boolean) => calls.push(`opacity:${id}:${value}:${live}`),
		onreopen: () => {},
		onseek: () => {},
		ontogglehidden: (id: string) => calls.push(`hide:${id}`),
		onremove: (id: string) => calls.push(`remove:${id}`),
		onmove: (id: string, to: number) => calls.push(`move:${id}:${to}`)
	});
	const q = <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel);
	const all = (sel: string) => Array.from(view.target.querySelectorAll<HTMLElement>(sel));
	return { calls, q, all };
}

const text = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();

describe('EditStack as layers', () => {
	it('splits the stack into a Layers panel with the original last and an Edits panel numbered newest first', () => {
		const { all, q } = render(layered(paint, crop, patch, warm));
		expect(all('[data-stack-section="layers"] [data-stack-row]').map((row) => row.dataset.stackRow)).toEqual(['s3', 's1', 'original']);
		expect(all('[data-stack-section="edits"] [data-stack-row]').map((row) => row.dataset.stackRow)).toEqual(['s4', 's2']);
		expect(all('[data-stack-section="edits"] [data-stack-number]').map((cell) => text(cell))).toEqual(['02', '01']);
		expect(q('[data-stack-section="layers"] [data-stack-number]')).toBeNull();
		expect(text(q('[data-stack-count="layers"]'))).toBe('2');
		expect(text(q('[data-stack-count="edits"]'))).toBe('2');
		expect(text(q('[data-stack-edits-hint]'))).toBe('Edits apply to the whole layered picture.');
	});

	it('says what edits do when there are none yet', () => {
		const { all, q } = render(layered(paint));
		expect(all('[data-stack-section="edits"] [data-stack-row]')).toHaveLength(0);
		expect(text(q('[data-stack-edits-hint]'))).toContain('No edits yet');
	});

	it('draws layer rows with an opacity value and edit rows without one', () => {
		const { q } = render(setOpacity(layered(paint, crop, patch), 's3', 0.5));
		expect(q('[data-stack-row="s1"]')!.dataset.stackKind).toBe('layer');
		expect(q('[data-stack-row="s3"]')!.dataset.stackKind).toBe('layer');
		expect(q('[data-stack-row="s2"]')!.dataset.stackKind).toBe('slim');
		expect(text(q('[data-stack-opacity="s3"]'))).toBe('50%');
		expect(text(q('[data-stack-opacity="s1"]'))).toBe('100%');
		expect(q('[data-stack-opacity="s2"]')).toBeNull();
		expect(q('[data-stack-row="s1"]')!.className).toContain('h-11');
		expect(q('[data-stack-row="s2"]')!.className).toContain('h-10');
	});

	it('gives every row an eye', () => {
		const { q, calls } = render(layered(paint, crop, warm));
		for (const id of ['s1', 's2', 's3']) expect(q(`[data-stack-hide="${id}"]`)).not.toBeNull();
		q<HTMLButtonElement>('[data-stack-hide="s1"]')!.click();
		expect(calls).toEqual(['hide:s1']);
	});

	it('no longer explains which layers an edit reaches', () => {
		const { all, q } = render(reopen(layered(paint, patch, warm), 's3'));
		expect(text(q('[data-stack-row="s3"]'))).not.toContain('Applies to');
		expect(all('[data-bracket]')).toHaveLength(0);
		expect(text(q('[data-stack-rows]'))).not.toContain('Not affected by');
	});

	it('moves a layer only among layers and an edit only among edits', () => {
		const stack = layered(paint, crop, patch, warm);
		const { q, calls } = render(stack);
		const key = (id: string, up: boolean) => q(`[data-stack-open="${id}"]`)!.dispatchEvent(new KeyboardEvent('keydown', { key: up ? 'ArrowUp' : 'ArrowDown', altKey: true, bubbles: true }));
		key('s1', true);
		key('s3', true);
		key('s2', true);
		key('s4', true);
		expect(calls).toEqual(['move:s1:1', 'move:s2:3']);
	});

	it('drags an edit within the Edits panel without a refusal line', async () => {
		const view = mountComponent(EditStack, {
			stack: layered(paint, crop, warm),
			original: { summary: '1024×1024 · PNG' },
			layered: true,
			isLayer: isLayerStep,
			layerThumb,
			pinned: [['paint', 'geometry']],
			onreopen: () => {},
			onseek: () => {},
			ontogglehidden: () => {},
			onremove: () => {},
			onmove: (id: string, to: number) => moves.push(`${id}:${to}`)
		});
		const moves: string[] = [];
		const grip = view.target.querySelector<HTMLElement>('[data-stack-grip="s3"]')!;
		const row = view.target.querySelector<HTMLElement>('[data-stack-row="s3"]')!;
		Object.defineProperty(row, 'offsetHeight', { value: 40 });
		grip.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientY: 100, bubbles: true }));
		grip.dispatchEvent(new PointerEvent('pointermove', { clientY: 200, bubbles: true }));
		await new Promise((resolve) => setTimeout(resolve, 0));
		expect(view.target.querySelector('[data-stack-refuse]')).toBeNull();
		grip.dispatchEvent(new PointerEvent('pointerup', { clientY: 200, bubbles: true }));
		expect(moves).toEqual(['s3:1']);
	});

	it('locks reordering when the stack keeps its earlier order', () => {
		const { q, calls } = render(build(crop, paint), { movable: false });
		expect(q('[data-stack-grip="s2"]')).toBeNull();
		q('[data-stack-open="s2"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', altKey: true, bubbles: true }));
		expect(calls).toEqual([]);
	});

	it('opens the opacity drawer under the selected layer only, with Move up, Move down and Delete', () => {
		const stack = build(paint, patch);
		expect(render(stack).q('[data-layer-drawer]')).toBeNull();
		const { q } = render(reopen(stack, 's2'));
		const drawer = q('[data-layer-drawer]')!;
		expect(drawer.dataset.layerDrawer).toBe('s2');
		expect(drawer.previousElementSibling).toBe(q('[data-stack-row="s2"]'));
		expect(drawer.querySelector('input[type="range"]')).not.toBeNull();
		expect(drawer.querySelector('[aria-label="Layer options for Add clouds"]')).not.toBeNull();
	});

	it('renames a layer from the row menu', async () => {
		const names: string[] = [];
		const stack = reopen(build(paint, patch), 's2');
		const view = mountComponent(EditStack, {
			stack,
			original: { summary: '1024×1024 · PNG' },
			layered: true,
			isLayer: isLayerStep,
			layerThumb,
				onreopen: () => {},
			onseek: () => {},
			ontogglehidden: () => {},
			onremove: () => {},
			onmove: () => {},
			onrename: (id: string, label: string) => names.push(`${id}:${label}`)
		});
		view.target.querySelector<HTMLButtonElement>('[aria-label="Layer options for Add clouds"]')!.click();
		await new Promise((resolve) => setTimeout(resolve, 0));
		const item = Array.from(document.querySelectorAll<HTMLElement>('[role="menuitem"]')).find((node) => node.textContent?.includes('Rename'));
		expect(item).toBeTruthy();
		item!.click();
		await new Promise((resolve) => setTimeout(resolve, 0));
		const input = view.target.querySelector<HTMLInputElement>('[data-stack-rename="s2"]')!;
		expect(input).not.toBeNull();
		await new Promise((resolve) => setTimeout(resolve, 5));
		expect(names).toEqual([]);
		input.value = 'Clouds';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		expect(names).toEqual(['s2:Clouds']);
	});

	it('shows the mask of a selected patch in its drawer and toggles Show mask', () => {
		const params = {
			v: 2,
			task: 'edit_region',
			prompt: 'clouds',
			setup: { preset_id: 'p', mode: 'editor:edit', form_data: {}, source: { kind: 'defaults', id: null, name: null }, label: '' },
			area: { region: { item_id: 'r1', url: 'data:image/png;base64,AA==' }, size: null },
			candidates: 1,
			results: [{ patch: { item_id: 'p1' }, url: 'u', seed: 1 }],
			chosen: 0,
			seed: 1,
			rect: { x: 0.1, y: 0.1, width: 0.5, height: 0.5 },
			soft: false,
			run: { id: 'er_1', status: 'done', error: null }
		};
		const stack = reopen(build(paint, { ...patch, params }), 's2');
		const toggles: string[] = [];
		let shown = false;
		const view = mountComponent(EditStack, {
			stack,
			original: { summary: '1024×1024 · PNG' },
			layered: true,
			selectedId: 's2',
			isLayer: isLayerStep,
			layerThumb,
				maskOf: patchMaskOf,
			get showMask() {
				return shown;
			},
			ontogglemask: () => toggles.push('toggle'),
			onreopen: () => {},
			onseek: () => {},
			ontogglehidden: () => {},
			onremove: () => {},
			onmove: () => {}
		});
		const drawer = view.target.querySelector('[data-layer-drawer="s2"]')!;
		expect(drawer.querySelector('[data-patch-mask-thumb] img')).not.toBeNull();
		expect(text(drawer.querySelector('[data-patch-mask]'))).toContain('Edited region');
		const button = drawer.querySelector<HTMLButtonElement>('[data-patch-mask-toggle="s2"]')!;
		expect(button.textContent?.trim()).toBe('Show mask');
		button.click();
		expect(toggles).toEqual(['toggle']);
		expect(view.target.querySelector('[data-layer-drawer="s1"]')).toBeNull();
	});
});
