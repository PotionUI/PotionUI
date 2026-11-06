import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import PreviewClipPlayer from '../../src/lib/components/workbench/PreviewClipPlayer.svelte';
import { mountComponent } from './helpers/mount';
import type { PreviewClip } from '../../src/lib/generation/previewClip';

function clip(step: number): PreviewClip {
	return {
		generation_id: 'g1',
		image: 'data:image/webp;base64,AAAA',
		frames: 25,
		columns: 5,
		frame_width: 384,
		frame_height: 216,
		fps: 8,
		step,
		total_steps: 30,
		source_indices: [],
		admin_detail: null
	};
}

function space(target: EventTarget): KeyboardEvent {
	const event = new KeyboardEvent('keydown', { key: ' ', code: 'Space', bubbles: true, cancelable: true });
	target.dispatchEvent(event);
	flushSync();
	return event;
}

function hover(root: Element) {
	root.dispatchEvent(new MouseEvent('mouseenter'));
}

afterEach(() => {
	vi.unstubAllGlobals();
	document.body.querySelectorAll('textarea, input').forEach((el) => el.remove());
});

describe('PreviewClipPlayer', () => {
	it('pauses on Space over the body while hovered and swallows the key', () => {
		const view = mountComponent(PreviewClipPlayer, { clip: clip(3) });
		const root = view.target.querySelector('[data-preview-clip]')!;
		hover(root);
		expect(root.getAttribute('data-paused')).toBe('false');
		const event = space(document.body);
		expect(root.getAttribute('data-paused')).toBe('true');
		expect(event.defaultPrevented).toBe(true);
		space(document.body);
		expect(root.getAttribute('data-paused')).toBe('false');
	});

	it('leaves Space alone while typing in a textarea or input', () => {
		const view = mountComponent(PreviewClipPlayer, { clip: clip(3) });
		const root = view.target.querySelector('[data-preview-clip]')!;
		hover(root);
		for (const tag of ['textarea', 'input']) {
			const field = document.body.appendChild(document.createElement(tag));
			const event = space(field);
			expect(root.getAttribute('data-paused')).toBe('false');
			expect(event.defaultPrevented).toBe(false);
		}
	});

	it('ignores Space when the pointer is not over the preview', () => {
		const view = mountComponent(PreviewClipPlayer, { clip: clip(3) });
		const root = view.target.querySelector('[data-preview-clip]')!;
		const event = space(document.body);
		expect(root.getAttribute('data-paused')).toBe('false');
		expect(event.defaultPrevented).toBe(false);
	});

	it('cancels its animation frame when it unmounts', () => {
		const cancel = vi.fn();
		vi.stubGlobal('requestAnimationFrame', () => 41);
		vi.stubGlobal('cancelAnimationFrame', cancel);
		const view = mountComponent(PreviewClipPlayer, { clip: clip(3) });
		expect(cancel).not.toHaveBeenCalled();
		view.destroy();
		expect(cancel).toHaveBeenCalledWith(41);
	});

	it('keeps the same canvas when a newer clip arrives', () => {
		const view = mountComponent(PreviewClipPlayer, { clip: clip(3) });
		const before = view.target.querySelector('canvas');
		expect(before).not.toBeNull();
		expect(view.target.querySelector('[data-preview-chip]')?.textContent).toMatch(/step 3/i);
		view.rerender({ clip: clip(6) });
		expect(view.target.querySelector('canvas')).toBe(before);
		expect(view.target.querySelector('[data-preview-chip]')?.textContent).toMatch(/step 6/i);
	});
});
