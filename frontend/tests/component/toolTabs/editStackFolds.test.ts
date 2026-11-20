import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { apply, beginEdit, createStack, type EditStackState, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import { arrangeLayers, isLayerStep, layerThumb } from '$lib/tool-tabs/image/layers';
import { STACK_FOLDS_KEY } from '$lib/tool-tabs/frame/stackFolds';

const { default: EditStack } = await import('$lib/components/tool-tabs/frame/EditStack.svelte');
const { default: HeaderActionsHost } = await import('./StackFoldHost.svelte');

const paint: StepDraft = { tool: 'paint', icon: 'editor-paint', label: 'Sketch', summary: 'Paint layer', params: { url: 'data:image/png;base64,AA==' }, lane: 'paint' };
const crop: StepDraft = { tool: 'crop', icon: 'editor-crop', label: 'Crop', summary: '4:3', params: {}, lane: 'geometry' };
const warm: StepDraft = { tool: 'effects', icon: 'editor-effects', label: 'Warm light', summary: 'Intensity 80', params: {} };

function layered(...drafts: StepDraft[]): EditStackState {
	return drafts.reduce((state, draft) => arrangeLayers(apply(beginEdit(state, draft))), createStack());
}

function mountStack(extra: Record<string, unknown> = {}) {
	const view = mountComponent(HeaderActionsHost as never, {
		stack: layered(paint, crop, warm),
		isLayer: isLayerStep,
		layerThumb,
		...extra
	});
	const q = <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel);
	const all = (sel: string) => Array.from(view.target.querySelectorAll<HTMLElement>(sel));
	return { view, q, all };
}

const text = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();

beforeEach(() => {
	localStorage.clear();
});

afterEach(() => {
	localStorage.clear();
});

describe('folding the Layers and Edits panels', () => {
	it('starts with both panels open', () => {
		const { q } = mountStack();
		expect(q('[data-stack-fold="layers"]')!.getAttribute('aria-expanded')).toBe('true');
		expect(q('[data-stack-fold="edits"]')!.getAttribute('aria-expanded')).toBe('true');
		expect(q('[data-stack-section="layers"]')).not.toBeNull();
		expect(q('[data-stack-section="edits"]')).not.toBeNull();
	});

	it('has one fold control per header and no whole-area chevron', () => {
		const { all } = mountStack();
		expect(all('[data-stack-fold]').map((node) => node.dataset.stackFold)).toEqual(['layers', 'edits']);
	});

	it('folds a panel when its header is clicked and keeps the other open', () => {
		const { q } = mountStack();
		q<HTMLButtonElement>('[data-stack-fold="edits"]')!.click();
		flushSync();
		expect(q('[data-stack-section="edits"]')).toBeNull();
		expect(q('[data-stack-edits-hint]')).toBeNull();
		expect(q('[data-stack-section="layers"]')).not.toBeNull();
		expect(q('[data-stack-fold="edits"]')!.getAttribute('aria-expanded')).toBe('false');
		expect(q('[data-stack-fold="layers"]')!.getAttribute('aria-expanded')).toBe('true');
		q<HTMLButtonElement>('[data-stack-fold="edits"]')!.click();
		flushSync();
		expect(q('[data-stack-section="edits"]')).not.toBeNull();
	});

	it('folds the Layers panel on its own', () => {
		const { q } = mountStack();
		q<HTMLButtonElement>('[data-stack-fold="layers"]')!.click();
		flushSync();
		expect(q('[data-stack-section="layers"]')).toBeNull();
		expect(q('[data-stack-section="edits"]')).not.toBeNull();
	});

	it('keeps the count in a folded header', () => {
		const { q } = mountStack();
		q<HTMLButtonElement>('[data-stack-fold="layers"]')!.click();
		q<HTMLButtonElement>('[data-stack-fold="edits"]')!.click();
		flushSync();
		expect(text(q('[data-stack-count="layers"]'))).toBe('1');
		expect(text(q('[data-stack-count="edits"]'))).toBe('2');
	});

	it('is a real button, so Enter and Space fold it', () => {
		const { q } = mountStack();
		const control = q<HTMLButtonElement>('[data-stack-fold="edits"]')!;
		expect(control.tagName).toBe('BUTTON');
		expect(control.getAttribute('type')).toBe('button');
		expect(control.getAttribute('aria-label')).toBe('Edits');
	});

	it('does not fold when a header button is pressed', () => {
		const { q } = mountStack();
		q<HTMLButtonElement>('[data-header-add]')!.click();
		flushSync();
		expect(q('[data-header-add]')!.dataset.pressed).toBe('1');
		expect(q('[data-stack-fold="layers"]')!.getAttribute('aria-expanded')).toBe('true');
		expect(q('[data-stack-section="layers"]')).not.toBeNull();
	});

	it('keeps the header buttons outside the fold control so a click never reaches it', () => {
		const { q } = mountStack();
		const control = q('[data-stack-fold="layers"]')!;
		expect(control.contains(q('[data-header-add]'))).toBe(false);
		expect(q('[data-stack-header-actions="layers"]')!.contains(q('[data-header-add]'))).toBe(true);
		expect(q('[data-stack-header-actions="layers"]')!.className).toContain('z-10');
	});

	it('remembers the folds for the next visit', () => {
		const first = mountStack();
		first.q<HTMLButtonElement>('[data-stack-fold="edits"]')!.click();
		flushSync();
		expect(JSON.parse(localStorage.getItem(STACK_FOLDS_KEY)!)).toEqual({ layers: false, edits: true });
		first.view.destroy();
		const second = mountStack();
		expect(second.q('[data-stack-section="edits"]')).toBeNull();
		expect(second.q('[data-stack-fold="edits"]')!.getAttribute('aria-expanded')).toBe('false');
		expect(second.q('[data-stack-section="layers"]')).not.toBeNull();
	});

	it('opens both when the stored value is unreadable', () => {
		localStorage.setItem(STACK_FOLDS_KEY, '{nope');
		const { q } = mountStack();
		expect(q('[data-stack-section="layers"]')).not.toBeNull();
		expect(q('[data-stack-section="edits"]')).not.toBeNull();
	});

	it('works without storage', () => {
		const original = Storage.prototype.getItem;
		const setter = Storage.prototype.setItem;
		Storage.prototype.getItem = () => {
			throw new Error('blocked');
		};
		Storage.prototype.setItem = () => {
			throw new Error('blocked');
		};
		try {
			const { q } = mountStack();
			q<HTMLButtonElement>('[data-stack-fold="layers"]')!.click();
			flushSync();
			expect(q('[data-stack-section="layers"]')).toBeNull();
		} finally {
			Storage.prototype.getItem = original;
			Storage.prototype.setItem = setter;
		}
	});

	it('folds the sections in the phone sheet too', () => {
		const { q } = mountStack({ variant: 'sheet' });
		q<HTMLButtonElement>('[data-stack-fold="edits"]')!.click();
		flushSync();
		expect(q('[data-stack-section="edits"]')).toBeNull();
		expect(q('[data-stack-section="layers"]')).not.toBeNull();
	});

	it('lets a folded header reopen the area when the whole stack is shut', () => {
		const { q, view } = mountStack({ collapsed: true });
		expect(q('[data-edit-stack]')!.hasAttribute('data-folded')).toBe(true);
		q<HTMLButtonElement>('[data-stack-fold="layers"]')!.click();
		flushSync();
		view.rerender({});
		expect(q('[data-edit-stack]')!.hasAttribute('data-folded')).toBe(false);
	});
});
