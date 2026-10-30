// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { keyboardFocus } from './helpers/keyboardFocus';

const { default: tooltip } = await import('$lib/actions/tooltip');
const { default: TooltipWrapFixture } = await import('./fixtures/TooltipWrapFixture.svelte');
const { createClassComponent } = await import('svelte/legacy');

let cleanup: Array<() => void> = [];

function attach(param: Parameters<typeof tooltip>[1], tag = 'button') {
	const node = document.createElement(tag);
	node.textContent = 'x';
	document.body.appendChild(node);
	const action = tooltip(node, param);
	cleanup.push(() => {
		action.destroy();
		node.remove();
	});
	return { node, action };
}

const settle = () => new Promise((r) => setTimeout(r, 0));
const bubbleText = () => document.body.querySelector('[role="tooltip"]')?.textContent?.trim() ?? null;

afterEach(() => {
	cleanup.forEach((fn) => fn());
	cleanup = [];
	vi.useRealTimers();
});

describe('tooltip action', () => {
	it('shows after the hover delay and hides on mouseleave', async () => {
		vi.useFakeTimers();
		const { node } = attach('Copy');
		node.dispatchEvent(new MouseEvent('mouseenter'));
		expect(bubbleText()).toBeNull();
		vi.advanceTimersByTime(250);
		await Promise.resolve();
		expect(bubbleText()).toBe('Copy');
		node.dispatchEvent(new MouseEvent('mouseleave'));
		expect(bubbleText()).toBeNull();
	});

	it('shows immediately on keyboard focus and describes the element while open', async () => {
		const { node } = attach('Delete item');
		keyboardFocus(node);
		await Promise.resolve();
		const bubble = document.body.querySelector('[role="tooltip"]')!;
		expect(bubble.textContent?.trim()).toBe('Delete item');
		expect(node.getAttribute('aria-describedby')).toBe(bubble.id);
		node.blur();
		await Promise.resolve();
		expect(bubbleText()).toBeNull();
		expect(node.hasAttribute('aria-describedby')).toBe(false);
	});

	it('closes on Escape', async () => {
		const { node } = attach('Delete item');
		keyboardFocus(node);
		await Promise.resolve();
		node.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
		expect(bubbleText()).toBeNull();
	});

	it('shows nothing for empty text', async () => {
		const { node } = attach(undefined);
		keyboardFocus(node);
		await Promise.resolve();
		expect(bubbleText()).toBeNull();
	});

	it('truncateOnly shows only when the content overflows', async () => {
		const { node } = attach({ text: 'A very long name', truncateOnly: true }, 'span');
		keyboardFocus(node);
		await Promise.resolve();
		expect(bubbleText()).toBeNull();
		Object.defineProperty(node, 'scrollWidth', { value: 200, configurable: true });
		Object.defineProperty(node, 'clientWidth', { value: 100, configurable: true });
		node.blur();
		keyboardFocus(node);
		await Promise.resolve();
		expect(bubbleText()).toBe('A very long name');
	});

	it('follows a changed text while open and removes the bubble on destroy', async () => {
		const { node, action } = attach('One');
		keyboardFocus(node);
		await Promise.resolve();
		action.update('Two');
		await Promise.resolve();
		expect(bubbleText()).toBe('Two');
		action.destroy();
		expect(bubbleText()).toBeNull();
	});

	it('wrap lets a long text break onto several lines', async () => {
		const { node } = attach({ text: 'Cinematic lighting studies, glass, liquids and reflections', wrap: true });
		keyboardFocus(node);
		await Promise.resolve();
		const style = document.body.querySelector<HTMLElement>('[role="tooltip"]')!.getAttribute('style') ?? '';
		expect(style).not.toContain('max-content');
		expect(style).toContain('max-width: min(360px');
	});

	it('keeps one line without wrap', async () => {
		const { node } = attach('Copy');
		keyboardFocus(node);
		await Promise.resolve();
		const style = document.body.querySelector<HTMLElement>('[role="tooltip"]')!.getAttribute('style') ?? '';
		expect(style).toContain('min-width: max-content');
	});

	it('focusSelf ignores focus that lands on a descendant', async () => {
		const { node } = attach({ text: 'Row name', focusSelf: true }, 'div');
		const child = document.createElement('button');
		node.appendChild(child);
		keyboardFocus(child);
		await Promise.resolve();
		expect(bubbleText()).toBeNull();
		keyboardFocus(node);
		await Promise.resolve();
		expect(bubbleText()).toBe('Row name');
	});
});

describe('tooltip action during teardown', () => {
	it('opens after a focus restore fired from a component teardown without an effect error', async () => {
		const { default: Fixture } = await import('./fixtures/TooltipFocusRestoreFixture.svelte');
		const { flushSync } = await import('svelte');
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = createClassComponent({ component: Fixture as never, target, props: { open: true } });
		cleanup.push(() => {
			component.$destroy();
			target.remove();
		});
		flushSync();
		expect(() => {
			component.$set({ open: false });
			flushSync();
		}).not.toThrow();
		await settle();
		expect(bubbleText()).toBe('Open dialog');
	});
});

describe('Tooltip wrapper', () => {
	function mountWrapper(props: Record<string, unknown>) {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = createClassComponent({
			component: TooltipWrapFixture as never,
			target,
			props
		});
		cleanup.push(() => {
			component.$destroy();
			target.remove();
		});
		return target;
	}

	it('opens on focusin of its trigger and closes on Escape', async () => {
		const target = mountWrapper({ text: 'Hint' });
		const trigger = target.querySelector('button')!.parentElement!;
		keyboardFocus(trigger);
		await new Promise((r) => setTimeout(r, 0));
		expect(bubbleText()).toBe('Hint');
		trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		await new Promise((r) => setTimeout(r, 0));
		expect(bubbleText()).toBeNull();
	});

	it('truncateOnly keeps a non-overflowing trigger silent', async () => {
		const target = mountWrapper({ text: 'Hint', truncateOnly: true });
		const trigger = target.querySelector('button')!.parentElement!;
		keyboardFocus(trigger);
		await new Promise((r) => setTimeout(r, 0));
		expect(bubbleText()).toBeNull();
	});
});
