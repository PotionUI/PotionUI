import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';

const { default: CanvasHost } = await import('./CanvasHost.svelte');

let restore: Array<() => void> = [];

function stubSize(prop: 'clientWidth' | 'clientHeight', value: number) {
	const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, prop);
	Object.defineProperty(HTMLElement.prototype, prop, { configurable: true, get: () => value });
	restore.push(() => {
		if (original) Object.defineProperty(HTMLElement.prototype, prop, original);
	});
}

beforeEach(() => {
	stubSize('clientWidth', 1120);
	stubSize('clientHeight', 620);
});

afterEach(() => {
	restore.forEach((fn) => fn());
	restore = [];
});

function render(props: Record<string, unknown> = {}) {
	const view = mountComponent(CanvasHost, props);
	const q = <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel);
	const state = () => JSON.parse(q('[data-host-state]')!.textContent!) as { viewport: { fit: boolean; zoom: number; panX: number; panY: number }; split: number };
	return { view, q, state };
}

function text(el: Element | null | undefined): string {
	return (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

describe('EditorCanvas zoom', () => {
	it('fits the media inside the padded stage on a black canvas', () => {
		const { q } = render();
		expect(q('[data-editor-canvas]')!.className).toContain('bg-scrim');
		const art = q('[data-canvas-art]')!.getAttribute('style')!;
		expect(art).toContain('width: 1000px');
		expect(art).toContain('left: 60px');
		expect(text(q('[data-canvas-zoom-value]'))).toBe('100%');
		expect(q('[data-canvas-fit]')!.getAttribute('aria-pressed')).toBe('true');
	});

	it('zooms in and out about the centre and leaves Fit', () => {
		const { q, state } = render();
		q<HTMLButtonElement>('[aria-label="Zoom in"]')!.click();
		flushSync();
		expect(text(q('[data-canvas-zoom-value]'))).toBe('125%');
		expect(state().viewport.fit).toBe(false);
		expect(q('[data-canvas-fit]')!.getAttribute('aria-pressed')).toBe('false');
		q<HTMLButtonElement>('[aria-label="Zoom out"]')!.click();
		q<HTMLButtonElement>('[aria-label="Zoom out"]')!.click();
		flushSync();
		expect(text(q('[data-canvas-zoom-value]'))).toBe('80%');
	});

	it('returns to Fit and shows 1:1 centred', () => {
		const { q, state } = render();
		q<HTMLButtonElement>('[data-canvas-actual]')!.click();
		flushSync();
		expect(state().viewport).toEqual({ fit: false, zoom: 1, panX: 60, panY: 60 });
		expect(q('[data-canvas-actual]')!.getAttribute('aria-pressed')).toBe('true');
		q<HTMLButtonElement>('[data-canvas-fit]')!.click();
		flushSync();
		expect(state().viewport.fit).toBe(true);
	});

	it('zooms with the wheel about the pointer', () => {
		const { q, state } = render();
		const stage = q('[data-editor-canvas]')!;
		const event = new WheelEvent('wheel', { deltaY: -200, clientX: 60, clientY: 60, cancelable: true });
		stage.dispatchEvent(event);
		flushSync();
		expect(event.defaultPrevented).toBe(true);
		const { viewport } = state();
		expect(viewport.zoom).toBeGreaterThan(1);
		expect(viewport.panX).toBeCloseTo(60);
		expect(viewport.panY).toBeCloseTo(60);
	});

	it('pans with a drag only while Space is held', () => {
		const { q, state, view } = render();
		const stage = q('[data-editor-canvas]')!;
		const drag = () => {
			stage.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientX: 100, clientY: 100, pointerType: 'mouse', bubbles: true }));
			stage.dispatchEvent(new PointerEvent('pointermove', { clientX: 130, clientY: 80, pointerType: 'mouse', bubbles: true }));
			stage.dispatchEvent(new PointerEvent('pointerup', { clientX: 130, clientY: 80, pointerType: 'mouse', bubbles: true }));
			flushSync();
		};
		drag();
		expect(state().viewport.fit).toBe(true);
		view.rerender({ spaceHeld: true });
		expect(stage.className).toContain('cursor-grab');
		drag();
		expect(state().viewport).toMatchObject({ fit: false, panX: 90, panY: 40 });
	});

	it('pinches with two fingers', () => {
		const { q, state } = render();
		const stage = q('[data-editor-canvas]')!;
		const touch = (type: string, id: number, x: number) =>
			stage.dispatchEvent(new PointerEvent(type, { pointerId: id, clientX: x, clientY: 300, pointerType: 'touch', bubbles: true }));
		touch('pointerdown', 1, 500);
		touch('pointerdown', 2, 600);
		touch('pointermove', 2, 700);
		flushSync();
		expect(state().viewport.zoom).toBeCloseTo(2);
		touch('pointerup', 1, 500);
		touch('pointerup', 2, 700);
	});

	it('drops the zoom bar and hint on a phone', () => {
		const { q } = render({ phone: true });
		expect(q('[data-canvas-zoom]')).toBeNull();
		expect(q('[data-canvas-hint]')).toBeNull();
	});

	it('shows the Space hint on desktop', () => {
		const { q } = render();
		const hint = q('[data-canvas-hint]')!;
		expect(text(hint.querySelector('[data-canvas-chip]'))).toBe('Space');
		expect(text(hint)).toContain('drag to pan');
	});
});

describe('EditorCanvas before and after', () => {
	it('shows the badge slot when not comparing', () => {
		const { q } = render();
		expect(text(q('[data-canvas-art]'))).toBe('Paused · 00:03.12');
		expect(q('[data-canvas-before]')).toBeNull();
	});

	it('clips the before layer at the split and labels both sides', () => {
		const { q } = render({ compare: true });
		expect(q('[data-canvas-before]')!.getAttribute('style')).toContain('inset(0 50% 0 0)');
		expect(text(q('[data-canvas-art]'))).toBe('Before After · 00:03.12');
		expect(q('[data-canvas-knob]')!.getAttribute('aria-valuenow')).toBe('50');
	});

	it('moves the split with the keyboard', () => {
		const { q, state } = render({ compare: true });
		const knob = q('[data-canvas-knob]')!;
		knob.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', shiftKey: true, bubbles: true }));
		flushSync();
		expect(state().split).toBeCloseTo(0.6);
		knob.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
		flushSync();
		expect(state().split).toBe(0);
		expect(q('[data-canvas-before]')!.getAttribute('style')).toContain('inset(0 100% 0 0)');
	});

	it('drags the split divider across the media', () => {
		const { q, state } = render({ compare: true });
		const divider = q('[data-canvas-divider]')!;
		divider.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientX: 310, bubbles: true }));
		divider.dispatchEvent(new PointerEvent('pointermove', { clientX: 860, bubbles: true }));
		divider.dispatchEvent(new PointerEvent('pointerup', { clientX: 860, bubbles: true }));
		flushSync();
		expect(state().split).toBeCloseTo(0.8);
	});
});
