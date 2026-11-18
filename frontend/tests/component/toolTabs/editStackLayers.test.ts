import { describe, expect, it } from 'vitest';
import { mountComponent } from '../helpers/mount';
import { apply, beginEdit, createStack, reopen, setOpacity, type EditStackState, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import { appliesBeneath, isLayerStep, layerThumb } from '$lib/tool-tabs/image/layers';

const { default: EditStack } = await import('$lib/components/tool-tabs/frame/EditStack.svelte');

const paint: StepDraft = { tool: 'paint', icon: 'editor-paint', label: 'Sketch', summary: 'Paint layer', params: { url: 'data:image/png;base64,AA==' }, lane: 'paint' };
const patch: StepDraft = { tool: 'generate', icon: 'editor-region', label: 'Add clouds', summary: 'Edit a region', params: {}, lane: 'paint' };
const crop: StepDraft = { tool: 'crop', icon: 'editor-crop', label: 'Crop', summary: '4:3', params: {}, lane: 'geometry' };
const warm: StepDraft = { tool: 'effects', icon: 'editor-effects', label: 'Warm light', summary: 'Intensity 80', params: {} };

function build(...drafts: StepDraft[]): EditStackState {
	return drafts.reduce((state, draft) => apply(beginEdit(state, draft)), createStack());
}

function render(stack: EditStackState) {
	const calls: string[] = [];
	const view = mountComponent(EditStack, {
		stack,
		original: { summary: '1024×1024 · PNG' },
		layered: true,
		isLayer: isLayerStep,
		layerThumb,
		appliesBeneath,
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
	it('lists the newest step on top and the original last, numbering unchanged', () => {
		const { all } = render(build(paint, crop, patch));
		expect(all('[data-stack-row]').map((row) => row.dataset.stackRow)).toEqual(['s3', 's2', 's1', 'original']);
		expect(all('[data-stack-row]').map((row) => text(row).slice(0, 2))).toEqual(['03', '02', '01', '00']);
	});

	it('draws pixel steps as layer rows with an opacity value and other steps as slim rows without one', () => {
		const { q } = render(setOpacity(build(paint, crop, patch), 's3', 0.5));
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
		const { q, calls } = render(build(paint, crop, warm));
		for (const id of ['s1', 's2', 's3']) expect(q(`[data-stack-hide="${id}"]`)).not.toBeNull();
		q<HTMLButtonElement>('[data-stack-hide="s1"]')!.click();
		expect(calls).toEqual(['hide:s1']);
	});

	it('says how many layers a look applies to, and brackets what is under it when it is selected', () => {
		const stack = build(paint, patch, warm);
		const idle = render(stack);
		expect(text(idle.q('[data-stack-row="s3"]'))).toContain('Applies to 3 layers below');
		expect(idle.all('[data-bracket]')).toHaveLength(0);

		const selected = render(reopen(stack, 's3'));
		expect(selected.all('[data-bracket]').map((row) => row.dataset.stackRow)).toEqual(['s3', 's2', 's1', 'original']);
	});

	it('tells the layers above a selected look that it does not touch them', () => {
		const { q } = render(reopen(build(paint, warm, patch), 's2'));
		expect(text(q('[data-stack-row="s3"]'))).toContain('Not affected by Warm light');
		expect(q('[data-stack-row="s3"]')!.dataset.bracket).toBeUndefined();
		expect(q('[data-stack-row="s1"]')!.dataset.bracket).toBeDefined();
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
});
