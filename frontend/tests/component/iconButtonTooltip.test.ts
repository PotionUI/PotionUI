import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync, type Component } from 'svelte';

const { default: IconButton } = await import('../../src/lib/components/ui/IconButton.svelte');
const { default: IconButtonInsideTooltipFixture } = await import('./fixtures/IconButtonInsideTooltipFixture.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render<Props extends Record<string, any>>(view: Component<Props>, props: Props) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(view, { target, props });
	flushSync();
}

function button(selector = 'button'): HTMLElement {
	return target.querySelector(selector) as HTMLElement;
}

function tooltips(): HTMLElement[] {
	return Array.from(document.body.querySelectorAll<HTMLElement>('[role="tooltip"]'));
}

async function wait(ms: number) {
	await new Promise((resolve) => setTimeout(resolve, ms));
	flushSync();
}

function hover(element: HTMLElement) {
	element.dispatchEvent(new MouseEvent('mouseenter'));
}

function unhover(element: HTMLElement) {
	element.dispatchEvent(new MouseEvent('mouseleave'));
	flushSync();
}

function focusVisible(element: HTMLElement, visible: boolean) {
	const matches = element.matches.bind(element);
	element.matches = (selector: string) =>
		selector === ':focus-visible' ? visible && document.activeElement === element : matches(selector);
}

async function keyboardFocus(element: HTMLElement) {
	focusVisible(element, true);
	element.focus();
	await wait(0);
}

async function pointerFocus(element: HTMLElement) {
	focusVisible(element, false);
	element.dispatchEvent(new Event('pointerdown', { bubbles: true }));
	element.focus();
	await wait(0);
}

async function blur(element: HTMLElement) {
	element.blur();
	await Promise.resolve();
	flushSync();
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
	document.body.querySelectorAll('[role="tooltip"]').forEach((node) => node.remove());
});

describe('IconButton without tooltip props', () => {
	it('is a lone button that shows its label in the host tooltip instead of a native title', async () => {
		render(IconButton, { icon: 'trash', label: 'Delete' });

		expect(button().parentElement).toBe(target);
		expect(button().hasAttribute('title')).toBe(false);
		expect(button().getAttribute('aria-label')).toBe('Delete');

		hover(button());
		await wait(250);

		const [tooltip] = tooltips();
		expect(tooltip.textContent?.trim()).toBe('Delete');
		expect(tooltip.querySelector('kbd')).toBeNull();
		expect(button().getAttribute('aria-describedby')).toBe(tooltip.id);
	});
});

describe('IconButton with tooltip or kbd', () => {
	it('shows its tooltip text instead of the label', async () => {
		render(IconButton, { icon: 'film', label: 'Render', tooltip: 'Render the selected take' });
		const trigger = button();

		expect(trigger.parentElement).toBe(target);
		expect(trigger.hasAttribute('title')).toBe(false);
		expect(trigger.getAttribute('aria-label')).toBe('Render');
		expect(tooltips()).toEqual([]);

		hover(trigger);
		await wait(250);

		const [tooltip] = tooltips();
		expect(tooltip.textContent?.trim()).toBe('Render the selected take');
		expect(trigger.getAttribute('aria-describedby')).toBe(tooltip.id);

		unhover(trigger);
		expect(tooltips()).toEqual([]);
		expect(trigger.hasAttribute('aria-describedby')).toBe(false);
	});

	it('falls back to the label and shows the shortcut, also to screen readers', async () => {
		render(IconButton, { icon: 'play', label: 'Play', kbd: ['Shift', 'Space'] });
		const trigger = button();

		await keyboardFocus(trigger);

		const [tooltip] = tooltips();
		expect(tooltip.querySelector('span')?.textContent).toBe('Play');
		expect(Array.from(tooltip.querySelectorAll('kbd')).map((key) => key.textContent?.trim())).toEqual([
			'Shift',
			'Space'
		]);
		expect(tooltip.querySelector('.sr-only')?.textContent).toBe('Shift+Space');
		expect(trigger.getAttribute('aria-describedby')).toBe(tooltip.id);
	});

	it('reads a shortcut given as one string as it is', async () => {
		render(IconButton, { icon: 'search', label: 'Search', kbd: 'Ctrl+K' });

		await keyboardFocus(button());

		const [tooltip] = tooltips();
		expect(Array.from(tooltip.querySelectorAll('kbd')).map((key) => key.textContent?.trim())).toEqual(['Ctrl+K']);
		expect(tooltip.querySelector('.sr-only')?.textContent).toBe('Ctrl+K');
	});

	it('shows at once on keyboard focus and hides on blur', async () => {
		render(IconButton, { icon: 'trash', label: 'Delete', tooltip: 'Delete the shot', kbd: 'Del' });
		const trigger = button();

		await keyboardFocus(trigger);
		expect(tooltips()).toHaveLength(1);

		await blur(trigger);
		expect(tooltips()).toEqual([]);
	});

	it('does not show when the focus follows a pointer press, then shows on keyboard focus', async () => {
		render(IconButton, { icon: 'trash', label: 'Delete', tooltip: 'Delete the shot' });
		const trigger = button();

		await pointerFocus(trigger);
		expect(tooltips()).toEqual([]);
		expect(trigger.hasAttribute('aria-describedby')).toBe(false);

		await blur(trigger);
		await keyboardFocus(trigger);
		expect(tooltips()).toHaveLength(1);
	});

	it('passes its button props through without leaking tooltip or kbd into the DOM', () => {
		render(IconButton, {
			icon: 'trash',
			label: 'Delete',
			tooltip: 'Delete',
			kbd: ['Shift', 'Del'],
			disabled: true,
			class: 'ml-2',
			'data-testid': 'delete-shot'
		});

		expect(button().hasAttribute('disabled')).toBe(true);
		expect(button().className).toContain('ml-2');
		expect(button().getAttribute('data-testid')).toBe('delete-shot');
		expect(button().hasAttribute('tooltip')).toBe(false);
		expect(button().hasAttribute('kbd')).toBe(false);
	});

	it('leaves the tooltip to an enclosing Tooltip of the same tree', async () => {
		render(IconButtonInsideTooltipFixture, {});
		const trigger = button('button[aria-label="Delete"]');

		await keyboardFocus(trigger);

		expect(tooltips().map((tooltip) => tooltip.textContent?.trim())).toEqual(['Delete the shot']);
	});
});
