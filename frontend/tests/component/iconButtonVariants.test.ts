// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { keyboardFocus } from './helpers/keyboardFocus';

const { default: IconButton } = await import('$lib/components/ui/IconButton.svelte');
const { mount, unmount, flushSync } = await import('svelte');

let handle: Record<string, unknown> | undefined;
let target: HTMLElement | undefined;

function render(props: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	handle = mount(IconButton as never, { target, props: { icon: 'close', label: 'Close', ...props } });
	flushSync();
	return target.querySelector('button')!;
}

afterEach(() => {
	if (handle) unmount(handle);
	target?.remove();
	handle = undefined;
});

describe('IconButton', () => {
	it('overlay variant uses the scrim tokens, not raw black or white', () => {
		const btn = render({ variant: 'overlay' });
		expect(btn.className).toContain('bg-scrim/60');
		expect(btn.className).toContain('text-scrim-on');
		expect(btn.className).not.toMatch(/bg-black|text-white/);
	});

	it('sets no native title and keeps the aria-label', () => {
		const btn = render({});
		expect(btn.hasAttribute('title')).toBe(false);
		expect(btn.getAttribute('aria-label')).toBe('Close');
	});

	it('shows its label as a tooltip on focus, outside a Tooltip', async () => {
		const btn = render({});
		keyboardFocus(btn);
		await Promise.resolve();
		expect(document.body.querySelector('[role="tooltip"]')?.textContent?.trim()).toBe('Close');
		btn.blur();
		await Promise.resolve();
		expect(document.body.querySelector('[role="tooltip"]')).toBeNull();
	});

	it('shows no second tooltip inside a Tooltip', async () => {
		const target2 = document.createElement('div');
		document.body.appendChild(target2);
		const { default: Fixture } = await import('./fixtures/TooltipIconButtonInnerFixture.svelte');
		const { createClassComponent } = await import('svelte/legacy');
		const h = createClassComponent({ component: Fixture as never, target: target2, props: {} });
		const btn = target2.querySelector('button')!;
		keyboardFocus(btn);
		await new Promise((r) => setTimeout(r, 0));
		expect(document.body.querySelectorAll('[role="tooltip"]').length).toBe(1);
		h.$destroy();
		target2.remove();
	});

	it('forwards data attributes and passes class through', () => {
		const btn = render({ 'data-testid': 'x', class: 'opacity-0 group-hover:opacity-100' });
		expect(btn.getAttribute('data-testid')).toBe('x');
		expect(btn.className).toContain('group-hover:opacity-100');
	});
});
