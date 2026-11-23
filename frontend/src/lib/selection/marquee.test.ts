// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { classifyCardSelectEvent, marqueeSelection, resolveCardSelect } from './marquee';

function eventLike(mods: Partial<Pick<MouseEvent, 'shiftKey' | 'ctrlKey' | 'metaKey'>>): MouseEvent {
	return { shiftKey: false, ctrlKey: false, metaKey: false, ...mods } as MouseEvent;
}

describe('classifyCardSelectEvent', () => {
	it('is plain when there is no event', () => {
		expect(classifyCardSelectEvent(undefined)).toBe('plain');
	});

	it('is plain when no modifier is held', () => {
		expect(classifyCardSelectEvent(eventLike({}))).toBe('plain');
	});

	it('is shift when the shift key is held', () => {
		expect(classifyCardSelectEvent(eventLike({ shiftKey: true }))).toBe('shift');
	});

	it('is ctrl when the ctrl key is held', () => {
		expect(classifyCardSelectEvent(eventLike({ ctrlKey: true }))).toBe('ctrl');
	});

	it('is ctrl when the meta (cmd) key is held', () => {
		expect(classifyCardSelectEvent(eventLike({ metaKey: true }))).toBe('ctrl');
	});

	it('prefers shift over ctrl when both are held', () => {
		expect(classifyCardSelectEvent(eventLike({ shiftKey: true, ctrlKey: true }))).toBe('shift');
	});
});

describe('resolveCardSelect', () => {
	const order = ['a', 'b', 'c', 'd'];

	it('plain toggles the target and moves the anchor onto it', () => {
		expect(resolveCardSelect('plain', order, 'a', 'c', ['a'])).toEqual({
			selection: ['a', 'c'],
			nextAnchorId: 'c'
		});
	});

	it('ctrl toggles the target and moves the anchor onto it, same as plain', () => {
		expect(resolveCardSelect('ctrl', order, 'a', 'c', ['a'])).toEqual({
			selection: ['a', 'c'],
			nextAnchorId: 'c'
		});
	});

	it('plain deselects an already-selected target', () => {
		expect(resolveCardSelect('plain', order, 'b', 'b', ['a', 'b'])).toEqual({
			selection: ['a'],
			nextAnchorId: 'b'
		});
	});

	it('shift selects the range from the anchor and leaves the anchor unchanged', () => {
		expect(resolveCardSelect('shift', order, 'a', 'c', [])).toEqual({
			selection: ['a', 'b', 'c'],
			nextAnchorId: 'a'
		});
	});

	it('shift with no anchor falls back to a plain toggle, anchor stays null', () => {
		expect(resolveCardSelect('shift', order, null, 'c', ['a'])).toEqual({
			selection: ['a', 'c'],
			nextAnchorId: null
		});
	});
});

describe('marqueeSelection', () => {
	function setupNode() {
		const node = document.createElement('div');
		node.setPointerCapture = vi.fn();
		node.hasPointerCapture = vi.fn(() => false);
		node.releasePointerCapture = vi.fn();
		document.body.appendChild(node);
		return node;
	}

	function pointerEvent(type: string, init: PointerEventInit) {
		return new PointerEvent(type, { bubbles: true, cancelable: true, button: 0, ...init });
	}

	function baseOptions(overrides: Partial<Parameters<typeof marqueeSelection>[1]> = {}) {
		return {
			orderedIds: [],
			selectedIds: [],
			cardSelector: '[data-card]',
			getCardId: (el: HTMLElement) => el.dataset.card,
			setSelection: vi.fn(),
			onSelectAll: vi.fn(),
			onChange: vi.fn(),
			...overrides
		};
	}

	it('a pointerdown/pointerup/click on a button inside the node reaches the button and starts no drag', () => {
		const node = setupNode();
		const button = document.createElement('button');
		node.appendChild(button);
		const buttonClick = vi.fn();
		button.addEventListener('click', buttonClick);

		const opts = baseOptions();
		const action = marqueeSelection(node, opts);

		button.dispatchEvent(pointerEvent('pointerdown', { clientX: 5, clientY: 5, pointerId: 1 }));
		button.dispatchEvent(pointerEvent('pointerup', { clientX: 5, clientY: 5, pointerId: 1 }));
		button.dispatchEvent(new MouseEvent('click', { bubbles: true }));

		expect(buttonClick).toHaveBeenCalledOnce();
		expect(node.setPointerCapture).not.toHaveBeenCalled();
		expect(opts.setSelection).not.toHaveBeenCalled();
		expect(opts.onChange).not.toHaveBeenCalledWith(expect.objectContaining({ active: true }));

		action.destroy();
	});

	it('captures the pointer only once a background drag crosses the threshold', () => {
		const node = setupNode();
		const opts = baseOptions();
		const action = marqueeSelection(node, opts);

		node.dispatchEvent(pointerEvent('pointerdown', { clientX: 100, clientY: 100, pointerId: 7 }));
		expect(node.setPointerCapture).not.toHaveBeenCalled();

		node.dispatchEvent(pointerEvent('pointermove', { clientX: 101, clientY: 100, pointerId: 7 }));
		expect(node.setPointerCapture).not.toHaveBeenCalled();

		node.dispatchEvent(pointerEvent('pointermove', { clientX: 110, clientY: 100, pointerId: 7 }));
		expect(node.setPointerCapture).toHaveBeenCalledExactlyOnceWith(7);

		action.destroy();
	});

	describe('frame-throttled selection', () => {
		function setupDrag(orderedIds: string[], cardRects: Record<string, DOMRect>) {
			const frames: FrameRequestCallback[] = [];
			vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => frames.push(cb));
			vi.stubGlobal('cancelAnimationFrame', () => {});
			const node = setupNode();
			for (const id of orderedIds) {
				const card = document.createElement('div');
				card.dataset.card = id;
				card.getBoundingClientRect = () => cardRects[id];
				node.appendChild(card);
			}
			const opts = baseOptions({ orderedIds });
			const action = marqueeSelection(node, opts);
			return { frames, node, opts, action };
		}

		const rect = (left: number, top: number, right: number, bottom: number) =>
			({ left, top, right, bottom, width: right - left, height: bottom - top }) as DOMRect;

		it('coalesces many pointermoves into one selection write per frame', () => {
			const { frames, node, opts, action } = setupDrag(['a', 'b'], {
				a: rect(0, 0, 10, 10),
				b: rect(20, 0, 30, 10)
			});
			node.dispatchEvent(pointerEvent('pointerdown', { clientX: 200, clientY: 200, pointerId: 1 }));
			for (let i = 0; i < 30; i++) {
				node.dispatchEvent(pointerEvent('pointermove', { clientX: 190 - i * 7, clientY: 190 - i * 7, pointerId: 1 }));
			}
			expect(frames).toHaveLength(1);
			expect(opts.setSelection).not.toHaveBeenCalled();
			frames[0](0);
			expect(opts.setSelection).toHaveBeenCalledTimes(1);
			expect(opts.setSelection).toHaveBeenCalledWith(['a', 'b']);
			action.destroy();
			vi.unstubAllGlobals();
		});

		it('does not rewrite the selection while the covered cards stay the same', () => {
			const { frames, node, opts, action } = setupDrag(['a'], { a: rect(0, 0, 10, 10) });
			node.dispatchEvent(pointerEvent('pointerdown', { clientX: 100, clientY: 100, pointerId: 1 }));
			node.dispatchEvent(pointerEvent('pointermove', { clientX: 5, clientY: 5, pointerId: 1 }));
			frames.shift()!(0);
			node.dispatchEvent(pointerEvent('pointermove', { clientX: 4, clientY: 4, pointerId: 1 }));
			frames.shift()!(0);
			expect(opts.setSelection).toHaveBeenCalledTimes(1);
			action.destroy();
			vi.unstubAllGlobals();
		});

		it('applies the last pointer position on pointerup without waiting for a frame', () => {
			const { frames, node, opts, action } = setupDrag(['a'], { a: rect(0, 0, 10, 10) });
			node.dispatchEvent(pointerEvent('pointerdown', { clientX: 100, clientY: 100, pointerId: 1 }));
			node.dispatchEvent(pointerEvent('pointermove', { clientX: 5, clientY: 5, pointerId: 1 }));
			node.dispatchEvent(pointerEvent('pointerup', { clientX: 5, clientY: 5, pointerId: 1 }));
			expect(frames).toHaveLength(1);
			expect(opts.setSelection).toHaveBeenCalledWith(['a']);
			action.destroy();
			vi.unstubAllGlobals();
		});
	});
});
