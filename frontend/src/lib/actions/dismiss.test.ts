// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$app/environment', () => ({ browser: true }));

import dismiss from './dismiss';
import { hasOpenDismissLayer, registerDismissLayer, resetLayerStackForTests } from './layerStack';

function mount(tag = 'div', parent: HTMLElement = document.body) {
	const el = document.createElement(tag);
	parent.appendChild(el);
	return el;
}

function pointerdown(target: Element) {
	target.dispatchEvent(new Event('pointerdown', { bubbles: true, cancelable: true }));
}

function escape(target: Element = document.body) {
	const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
	target.dispatchEvent(event);
	return event;
}

beforeEach(() => {
	resetLayerStackForTests();
	document.body.innerHTML = '';
});

afterEach(() => {
	resetLayerStackForTests();
});

describe('dismiss outside pointer', () => {
	it('fires for a pointerdown outside the node and not for one inside', () => {
		const node = mount();
		const child = mount('span', node);
		const outside = mount();
		const onDismiss = vi.fn();
		const action = dismiss(node, { onDismiss });

		pointerdown(child);
		expect(onDismiss).not.toHaveBeenCalled();
		pointerdown(outside);
		expect(onDismiss).toHaveBeenCalledWith('outside', expect.any(Event));
		action.destroy();
	});

	it('treats ignored elements as inside', () => {
		const node = mount();
		const trigger = mount('button');
		const onDismiss = vi.fn();
		const action = dismiss(node, { onDismiss, ignore: [trigger, null] });

		pointerdown(trigger);
		expect(onDismiss).not.toHaveBeenCalled();
		action.destroy();
	});

	it('honours outside:false', () => {
		const node = mount();
		const outside = mount();
		const onDismiss = vi.fn();
		const action = dismiss(node, { onDismiss, outside: false });

		pointerdown(outside);
		expect(onDismiss).not.toHaveBeenCalled();
		action.destroy();
	});

	it('skips pointerdowns inside a different modal only when skipModals is set', () => {
		const node = mount();
		const modal = mount();
		modal.setAttribute('aria-modal', 'true');
		modal.setAttribute('aria-label', 'Details');
		const inModal = mount('span', modal);
		const plain = vi.fn();
		const skipping = vi.fn();
		const a = dismiss(node, { onDismiss: plain });
		const b = dismiss(mount(), { onDismiss: skipping, skipModals: true });

		pointerdown(inModal);
		expect(plain).toHaveBeenCalledWith('outside', expect.any(Event));
		expect(skipping).not.toHaveBeenCalled();
		a.destroy();
		b.destroy();
	});

	it('still dismisses on the chat shell modal', () => {
		const node = mount();
		const shell = mount();
		shell.setAttribute('aria-modal', 'true');
		shell.setAttribute('aria-label', 'AI chat');
		const onDismiss = vi.fn();
		const action = dismiss(node, { onDismiss, skipModals: true });

		pointerdown(mount('span', shell));
		expect(onDismiss).toHaveBeenCalledWith('outside', expect.any(Event));
		action.destroy();
	});
});

describe('dismiss escape ordering', () => {
	it('only the topmost layer consumes Escape', () => {
		const lower = vi.fn();
		const upper = vi.fn();
		const a = dismiss(mount(), { onDismiss: lower });
		const b = dismiss(mount(), { onDismiss: upper });

		const event = escape();
		expect(upper).toHaveBeenCalledWith('escape', expect.any(Event));
		expect(lower).not.toHaveBeenCalled();
		expect(event.defaultPrevented).toBe(true);

		b.destroy();
		escape();
		expect(lower).toHaveBeenCalledWith('escape', expect.any(Event));
		a.destroy();
	});

	it('leaves Escape alone when the topmost layer is not a dismiss layer', () => {
		const lower = vi.fn();
		const a = dismiss(mount(), { onDismiss: lower });
		registerDismissLayer({ node: mount() });

		const event = escape();
		expect(lower).not.toHaveBeenCalled();
		expect(event.defaultPrevented).toBe(false);
		a.destroy();
	});

	it('does not consume Escape when escape is false', () => {
		const onDismiss = vi.fn();
		const a = dismiss(mount(), { onDismiss, escape: false });

		expect(escape().defaultPrevented).toBe(false);
		expect(onDismiss).not.toHaveBeenCalled();
		a.destroy();
	});

	it('leaves Escape to an element that keeps it, and still dismisses from elsewhere', () => {
		const onDismiss = vi.fn();
		const a = dismiss(mount(), { onDismiss });
		const input = mount('input');
		input.setAttribute('data-keeps-escape', '');
		const inner = mount('span');

		const kept = escape(input);
		expect(onDismiss).not.toHaveBeenCalled();
		expect(kept.defaultPrevented).toBe(false);

		escape(inner);
		expect(onDismiss).toHaveBeenCalledTimes(1);
		a.destroy();
	});

	it('ignores an Escape that was already handled', () => {
		const onDismiss = vi.fn();
		const a = dismiss(mount(), { onDismiss });
		const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
		event.preventDefault();
		document.body.dispatchEvent(event);

		expect(onDismiss).not.toHaveBeenCalled();
		a.destroy();
	});

	it('stops the event before bubble-phase window listeners', () => {
		const windowHandler = vi.fn();
		window.addEventListener('keydown', windowHandler);
		const a = dismiss(mount(), { onDismiss: vi.fn() });

		escape();
		expect(windowHandler).not.toHaveBeenCalled();
		a.destroy();
		escape();
		expect(windowHandler).toHaveBeenCalledTimes(1);
		window.removeEventListener('keydown', windowHandler);
	});
});

describe('dismiss nested layers', () => {
	it('a pointerdown inside the upper layer counts as inside for the lower one', () => {
		const lowerNode = mount();
		const upperNode = mount();
		const lower = vi.fn();
		const upper = vi.fn();
		const a = dismiss(lowerNode, { onDismiss: lower });
		const b = dismiss(upperNode, { onDismiss: upper });

		pointerdown(upperNode);
		expect(lower).not.toHaveBeenCalled();
		expect(upper).not.toHaveBeenCalled();

		pointerdown(lowerNode);
		expect(upper).toHaveBeenCalledWith('outside', expect.any(Event));
		expect(lower).not.toHaveBeenCalled();

		pointerdown(mount());
		expect(lower).toHaveBeenCalledWith('outside', expect.any(Event));
		a.destroy();
		b.destroy();
	});
});

describe('dismiss lifecycle', () => {
	it('removes the layer on destroy and picks up updated options', () => {
		const first = vi.fn();
		const second = vi.fn();
		const node = mount();
		const action = dismiss(node, { onDismiss: first });
		expect(hasOpenDismissLayer()).toBe(true);

		action.update({ onDismiss: second });
		escape();
		expect(first).not.toHaveBeenCalled();
		expect(second).toHaveBeenCalledWith('escape', expect.any(Event));

		action.destroy();
		expect(hasOpenDismissLayer()).toBe(false);
		second.mockClear();
		escape();
		pointerdown(mount());
		expect(second).not.toHaveBeenCalled();
	});
});
