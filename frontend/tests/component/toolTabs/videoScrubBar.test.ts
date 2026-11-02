import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';

const { default: ScrubBar } = await import('$lib/components/tool-tabs/video/ScrubBar.svelte');
const { default: RenderCard } = await import('$lib/components/tool-tabs/video/RenderCard.svelte');

function rect(el: Element, left: number, width: number) {
	vi.spyOn(el, 'getBoundingClientRect').mockReturnValue({ left, width, top: 0, height: 48, right: left + width, bottom: 48, x: left, y: 0, toJSON: () => ({}) } as DOMRect);
}

function pointer(type: string, clientX: number) {
	return new PointerEvent(type, { bubbles: true, button: 0, clientX, pointerId: 1 } as PointerEventInit);
}

function scrub(props: Record<string, unknown> = {}) {
	const calls: string[] = [];
	const view = mountComponent(ScrubBar, {
		duration: 8,
		playhead: 3.12,
		inPoint: 1.2,
		outPoint: 6.4,
		outputLength: 5.2,
		handles: true,
		active: true,
		compact: false,
		frameStep: 0.04,
		frames: ['data:image/png;base64,AA==', null, null],
		peaks: [0.2, 0.9, 0.4, 0.1],
		onseek: (t: number) => calls.push(`seek:${t.toFixed(2)}`),
		onin: (t: number) => calls.push(`in:${t.toFixed(2)}`),
		onout: (t: number) => calls.push(`out:${t.toFixed(2)}`),
		ontoggleplay: () => calls.push('play'),
		onstep: (d: number) => calls.push(`step:${d}`),
		ontoggleloop: () => calls.push('loop'),
		ontogglemute: () => calls.push('mute'),
		...props
	});
	const q = <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel);
	return { calls, view, q };
}

function styleNumber(el: Element | null, prop: string): number {
	const match = new RegExp(`${prop}:\\s*([\\d.]+)%`).exec(el?.getAttribute('style') ?? '');
	return match ? Number(match[1]) : Number.NaN;
}

function key(key: string, target: EventTarget = document.body, init: KeyboardEventInit = {}) {
	const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
	target.dispatchEvent(event);
	flushSync();
	return event;
}

afterEach(() => {
	vi.restoreAllMocks();
	document.body.innerHTML = '';
});

describe('ScrubBar readouts', () => {
	it('shows the playhead, IN, OUT and LENGTH in mono time', () => {
		const { q } = scrub();
		expect(q('[data-scrub-time]')!.textContent!.replace(/\s+/g, ' ').trim()).toBe('00:03.12 / 00:08.00');
		expect(q('[data-scrub-in]')!.textContent).toBe('00:01.20');
		expect(q('[data-scrub-out]')!.textContent).toBe('00:06.40');
		expect(q('[data-scrub-length]')!.textContent).toBe('5.20 s');
		expect(q('[data-scrub-readouts]')!.className).toContain('tabular-nums');
	});

	it('dims outside the range, draws white handles and a signal playhead', () => {
		const { q } = scrub();
		expect(styleNumber(q('[data-scrub-dim="in"]'), 'width')).toBeCloseTo(15, 6);
		expect(styleNumber(q('[data-scrub-dim="out"]'), 'width')).toBeCloseTo(20, 6);
		expect(q('[data-scrub-handle="in"]')!.className).toContain('bg-scrim-on');
		expect(q('[data-scrub-playhead]')!.className).toContain('bg-signal');
		expect(styleNumber(q('[data-scrub-playhead]'), 'left')).toBeCloseTo(39, 6);
		expect(q('[data-scrub-wave="ready"] svg')!.querySelectorAll('rect')).toHaveLength(4);
		expect(q('[data-scrub-frame]')).not.toBeNull();
	});

	it('hides the handles outside Trim but keeps the dimmed range', () => {
		const { q } = scrub({ handles: false });
		expect(q('[data-scrub-handle="in"]')).toBeNull();
		expect(q('[data-scrub-dim="in"]')).not.toBeNull();
	});

	it('shows the compact bar on a phone', () => {
		const { q } = scrub({ compact: true });
		expect(q('[data-scrub-bar="compact"]')).not.toBeNull();
		expect(q('[data-scrub-ruler]')).toBeNull();
		expect(q('[data-scrub-wave]')).toBeNull();
		expect(q('[data-scrub-length]')!.textContent).toBe('5.20 s');
	});
});

describe('ScrubBar handles', () => {
	it('drags the in handle along the strip', () => {
		const { q, calls } = scrub();
		rect(q('[data-scrub-track]')!, 100, 800);
		const handle = q('[data-scrub-handle="in"]')!;
		handle.dispatchEvent(pointer('pointerdown', 300));
		handle.dispatchEvent(pointer('pointermove', 340));
		handle.dispatchEvent(pointer('pointerup', 340));
		handle.dispatchEvent(pointer('pointermove', 600));
		expect(calls).toEqual(['in:2.00', 'in:2.40']);
	});

	it('drags the out handle and scrubs on the strip', () => {
		const { q, calls } = scrub();
		rect(q('[data-scrub-track]')!, 0, 800);
		const handle = q('[data-scrub-handle="out"]')!;
		handle.dispatchEvent(pointer('pointerdown', 500));
		handle.dispatchEvent(pointer('pointerup', 500));
		q('[data-scrub-track]')!.dispatchEvent(pointer('pointerdown', 200));
		expect(calls).toEqual(['out:5.00', 'seek:2.00']);
	});

	it('nudges a focused handle a frame with the arrows, a second with Shift', () => {
		const { q, calls } = scrub();
		const handle = q('[data-scrub-handle="in"]')!;
		handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
		handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', shiftKey: true, bubbles: true, cancelable: true }));
		expect(calls).toEqual(['in:1.24', 'in:0.20']);
	});
});

describe('ScrubBar keys', () => {
	it('K plays, I and O set the points at the playhead, arrows step a frame', () => {
		const { calls } = scrub();
		for (const k of ['k', 'i', 'o', 'ArrowLeft', 'ArrowRight']) expect(key(k).defaultPrevented).toBe(true);
		expect(calls).toEqual(['play', 'in:3.12', 'out:3.12', 'step:-1', 'step:1']);
	});

	it('leaves keys alone while typing, with a modifier, or when not active', () => {
		const { calls, view } = scrub();
		const input = document.createElement('input');
		document.body.appendChild(input);
		key('i', input);
		key('k', document.body, { ctrlKey: true });
		view.rerender({ active: false });
		key('o');
		expect(calls).toEqual([]);
	});

	it('runs the transport buttons', () => {
		const { q, calls } = scrub();
		q<HTMLButtonElement>('[data-scrub-play]')!.click();
		q<HTMLButtonElement>('[data-scrub-prev]')!.click();
		q<HTMLButtonElement>('[data-scrub-next]')!.click();
		q<HTMLButtonElement>('[data-scrub-loop]')!.click();
		q<HTMLButtonElement>('[data-scrub-mute]')!.click();
		expect(calls).toEqual(['play', 'step:-1', 'step:1', 'loop', 'mute']);
		expect(q('[data-scrub-play]')!.className).toContain('bg-accent');
	});
});

describe('RenderCard', () => {
	it('shows the rendered time, output and time left in the panel', () => {
		const view = mountComponent(RenderCard, { percent: 42, total: 5.2, output: 'MP4 · 1920×1080', left: 14.2 });
		const text = view.target.textContent!.replace(/\s+/g, ' ');
		expect(view.target.querySelector('[data-render-percent]')!.textContent).toBe('42%');
		expect(view.target.querySelector('[data-render-rendered]')!.textContent).toBe('00:02.18 of 00:05.20');
		expect(view.target.querySelector('[data-render-output]')!.textContent).toBe('MP4 · 1920×1080');
		expect(view.target.querySelector('[data-render-left]')!.textContent).toBe('about 14 s');
		expect(view.target.querySelector('[role="progressbar"]')!.getAttribute('aria-valuenow')).toBe('42');
		expect(text).toContain('Saving as new');
		expect(view.target.querySelector('[data-render-cancel]')).toBeNull();
	});

	it('holds Cancel on a phone and drops the output row', () => {
		const cancelled: number[] = [];
		const view = mountComponent(RenderCard, { phone: true, percent: 10, total: 4, output: 'MP4', left: null, oncancel: () => cancelled.push(1) });
		expect(view.target.querySelector('[data-render-output]')).toBeNull();
		expect(view.target.querySelector('[data-render-left]')!.textContent).toBe('Working it out');
		view.target.querySelector<HTMLButtonElement>('[data-render-cancel]')!.click();
		expect(cancelled).toEqual([1]);
	});

	it('says Cancelling while the cancel is in flight', () => {
		const view = mountComponent(RenderCard, { phone: true, percent: 10, total: 4, output: 'MP4', cancelling: true });
		const button = view.target.querySelector<HTMLButtonElement>('[data-render-cancel]')!;
		expect(button.disabled).toBe(true);
		expect(button.textContent).toContain('Cancelling');
	});
});
