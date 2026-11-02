import { describe, expect, it } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import {
	apply,
	beginEdit,
	createStack,
	reopen,
	toggleHidden,
	undo,
	type EditStackState,
	type StepDraft
} from '$lib/tool-tabs/frame/editStack';

const { default: EditStack } = await import('$lib/components/tool-tabs/frame/EditStack.svelte');

const crop: StepDraft = { tool: 'crop', icon: 'editor-crop', label: 'Crop', summary: '4:3 · 896×672', params: {}, lane: 'geometry' };
const resize: StepDraft = { tool: 'resize', icon: 'editor-resize', label: 'Resize', summary: '1536×1152 · Lanczos', params: {}, lane: 'geometry' };
const reel: StepDraft = { tool: 'effects', icon: 'editor-effects', label: 'Reel', summary: 'Intensity 60', params: {}, lane: 'look' };
const paint: StepDraft = { tool: 'paint', icon: 'editor-paint', label: 'Paint', summary: '2 layers', params: {}, lane: 'paint' };

function build(...drafts: StepDraft[]): EditStackState {
	return drafts.reduce((state, draft) => apply(beginEdit(state, draft)), createStack());
}

function render(stack: EditStackState, props: Record<string, unknown> = {}) {
	const calls: string[] = [];
	const view = mountComponent(EditStack, {
		stack,
		original: { summary: '1024×1024 · PNG' },
		onreopen: (id: string) => calls.push(`reopen:${id}`),
		onseek: (cursor: number) => calls.push(`seek:${cursor}`),
		ontogglehidden: (id: string) => calls.push(`hide:${id}`),
		onremove: (id: string) => calls.push(`remove:${id}`),
		onmove: (id: string, to: number) => calls.push(`move:${id}:${to}`),
		onundo: () => calls.push('undo'),
		onredo: () => calls.push('redo'),
		...props
	});
	const q = <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel);
	const row = (key: string) => q(`[data-stack-row="${key}"]`)!;
	return { calls, view, q, row };
}

function text(el: Element | null | undefined): string {
	return (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

describe('EditStack rows', () => {
	it('pins the original as row 00 and numbers the steps', () => {
		const { view, row } = render(build(crop, resize));
		const rows = Array.from(view.target.querySelectorAll('[data-stack-row]'));
		expect(rows.map((el) => text(el).slice(0, 2))).toEqual(['00', '01', '02']);
		expect(text(row('original'))).toContain('Original');
		expect(text(row('original'))).toContain('1024×1024 · PNG');
		expect(row('original').querySelector('[data-stack-grip] svg, [data-stack-open]')).toBeNull();
		expect(text(row('s1'))).toContain('4:3 · 896×672');
	});

	it('marks the step at the cursor current', () => {
		const { row } = render(build(crop, resize));
		expect(row('s2').dataset.status).toBe('current');
		expect(row('s2').className).toContain('is-current');
		expect(row('s1').dataset.status).toBe('applied');
	});

	it('greys undone steps, says Undone, counts them and explains redo', () => {
		const { row, q } = render(undo(build(crop, resize, reel)));
		expect(row('s3').dataset.status).toBe('undone');
		expect(row('s3').className).toContain('opacity-[0.45]');
		expect(text(row('s3'))).toContain('Undone');
		expect(text(q('[data-stack-count]'))).toBe('2 + 1 undone');
		expect(text(q('[data-stack-note="undone"]'))).toBe('Showing step 02. Redo brings Reel back; a new edit drops it.');
		expect(row('s3').querySelector('[data-stack-actions]')).toBeNull();
	});

	it('shows a new edit as a PENDING row and hides the undone note', () => {
		const { row, q } = render(beginEdit(build(crop), resize));
		expect(row('pending').dataset.status).toBe('pending');
		expect(text(row('pending'))).toContain('PENDING');
		expect(text(row('pending'))).toContain('02');
		expect(row('pending').className).toContain('border-dashed');
		expect(q('[data-stack-note="undone"]')).toBeNull();
	});

	it('shows a reopened step as PENDING in place', () => {
		const { row } = render(reopen(build(crop, resize), 's1'));
		expect(row('s1').dataset.status).toBe('pending');
		expect(text(row('s1'))).toContain('PENDING');
		expect(row('s1').querySelector('[data-stack-open]')).toBeNull();
	});

	it('dims a hidden step and keeps its eye visible', () => {
		const { row } = render(toggleHidden(build(crop, resize), 's1'));
		expect(row('s1').hasAttribute('data-hidden')).toBe(true);
		expect(row('s1').querySelector('[data-stack-actions]')!.className).not.toContain('opacity-0');
		expect(row('s1').querySelector('[data-stack-hide]')!.getAttribute('aria-label')).toBe('Show Crop');
	});

	it('renders an original thumbnail when given one', () => {
		const { row } = render(build(crop), { original: { summary: 'x', thumb: '/thumb.webp' } });
		expect(row('original').innerHTML).toContain('/thumb.webp');
	});
});

describe('EditStack actions', () => {
	it('reopens an applied step on click and redoes up to an undone one', () => {
		const { row, calls } = render(undo(build(crop, resize, reel)));
		row('s1').querySelector<HTMLButtonElement>('[data-stack-open]')!.click();
		row('s3').querySelector<HTMLButtonElement>('[data-stack-open]')!.click();
		expect(calls).toEqual(['reopen:s1', 'seek:3']);
		expect(row('s3').querySelector('[data-stack-open]')!.getAttribute('aria-label')).toBe('Redo to Reel');
	});

	it('hides and removes a step from its row actions', () => {
		const { row, calls } = render(build(crop, resize));
		row('s2').querySelector<HTMLButtonElement>('[data-stack-hide]')!.click();
		row('s2').querySelector<HTMLButtonElement>('[data-stack-remove]')!.click();
		expect(calls).toEqual(['hide:s2', 'remove:s2']);
		expect(row('s2').querySelector('[data-stack-remove]')!.getAttribute('aria-label')).toBe('Remove Resize');
	});

	it('moves a step with Alt and the arrow keys within the pinning rules', () => {
		const { row, calls } = render(build(crop, paint, reel));
		const key = (id: string, k: string) =>
			row(id).querySelector('[data-stack-open]')!.dispatchEvent(new KeyboardEvent('keydown', { key: k, altKey: true, bubbles: true }));
		key('s3', 'ArrowUp');
		key('s2', 'ArrowUp');
		key('s1', 'ArrowUp');
		expect(calls).toEqual(['move:s3:1']);
	});

	it('drags a step by its grip to a new index', () => {
		const { row, calls } = render(build(crop, resize, reel));
		const grip = row('s3').querySelector<HTMLElement>('[data-stack-grip]')!;
		grip.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientY: 200, bubbles: true }));
		grip.dispatchEvent(new PointerEvent('pointermove', { clientY: 112, bubbles: true }));
		flushSync();
		expect(row('s3').getAttribute('style')).toContain('translateY(-88px)');
		grip.dispatchEvent(new PointerEvent('pointerup', { clientY: 112, bubbles: true }));
		flushSync();
		expect(calls).toEqual(['move:s3:0']);
		expect(row('s3').getAttribute('style') ?? '').not.toContain('translateY');
	});

	it('refuses a drop that breaks the paint and geometry order', () => {
		const { row, calls } = render(build(crop, paint));
		const grip = row('s2').querySelector<HTMLElement>('[data-stack-grip]')!;
		grip.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientY: 100, bubbles: true }));
		grip.dispatchEvent(new PointerEvent('pointermove', { clientY: 56, bubbles: true }));
		flushSync();
		expect(row('s2').hasAttribute('data-drop-blocked')).toBe(true);
		grip.dispatchEvent(new PointerEvent('pointerup', { clientY: 56, bubbles: true }));
		expect(calls).toEqual([]);
	});

	it('offers no drag on the original, undone or reopened rows', () => {
		const { row } = render(reopen(undo(build(crop, resize, reel)), 's1'));
		for (const key of ['original', 's1', 's2', 's3']) expect(row(key).querySelector('[data-stack-grip]'), key).toBeNull();
	});
});

describe('EditStack as a phone sheet', () => {
	it('heads with undo and redo and drops the row actions', () => {
		const { q, view, calls } = render(undo(build(crop, resize)), { variant: 'sheet' });
		expect(text(q('h2'))).toBe('Edits');
		expect(text(q('[data-stack-count]'))).toBe('1');
		expect(view.target.querySelector('[data-stack-actions]')).toBeNull();
		const buttons = Array.from(view.target.querySelectorAll<HTMLButtonElement>('h2 ~ button'));
		expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual(['Undo Crop', 'Redo Resize']);
		buttons.forEach((b) => b.click());
		expect(calls).toEqual(['undo', 'redo']);
	});
});
