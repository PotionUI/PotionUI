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

	it('renames a layer from the row menu', async () => {
		const names: string[] = [];
		const stack = reopen(build(paint, patch), 's2');
		const view = mountComponent(EditStack, {
			stack,
			original: { summary: '1024×1024 · PNG' },
			layered: true,
			isLayer: isLayerStep,
			layerThumb,
			appliesBeneath,
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

	it('shows a red dashed drop line with the reason when a drop is refused', async () => {
		const view = mountComponent(EditStack, {
			stack: build(crop, paint),
			original: { summary: '1024×1024 · PNG' },
			layered: true,
			isLayer: isLayerStep,
			layerThumb,
			appliesBeneath,
			pinned: [['paint', 'geometry']],
			onreopen: () => {},
			onseek: () => {},
			ontogglehidden: () => {},
			onremove: () => {},
			onmove: () => {}
		});
		const grip = view.target.querySelector<HTMLElement>('[data-stack-grip="s2"]')!;
		const row = view.target.querySelector<HTMLElement>('[data-stack-row="s2"]')!;
		Object.defineProperty(row, 'offsetHeight', { value: 44 });
		grip.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientY: 100, bubbles: true }));
		grip.dispatchEvent(new PointerEvent('pointermove', { clientY: 150, bubbles: true }));
		await new Promise((resolve) => setTimeout(resolve, 0));
		const refuse = view.target.querySelector('[data-stack-refuse]');
		expect(refuse).not.toBeNull();
		expect(refuse!.querySelector('[data-stack-dropline="blocked"]')!.className).toContain('border-danger');
		expect(text(refuse)).toContain("Sketch can't go below Crop");
	});
});
