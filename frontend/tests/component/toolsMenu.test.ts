// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import ToolsMenu from '$lib/components/tools/ToolsMenu.svelte';
import type { MediaTool, MediaToolGroup } from '$lib/tools/tools';

function tool(id: string, extra: Partial<MediaTool> = {}): MediaTool {
	return {
		id,
		label: id.toUpperCase(),
		icon: 'extension',
		category: 'edit',
		source: 'core',
		scopes: ['history', 'field'],
		applies: () => ({ enabled: true }),
		...extra
	};
}

function groups(): MediaToolGroup[] {
	return [
		{
			category: { id: 'edit', label: 'Edit', order: 10 },
			tools: [
				{ tool: tool('crop', { shortcut: 'C', description: 'Crop it' }), availability: { enabled: true } },
				{ tool: tool('clear'), availability: { enabled: false, reason: 'No mask' } }
			]
		}
	];
}

let cleanup: (() => void) | undefined;

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

function render(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = mount(ToolsMenu, {
		target,
		props: {
			open: true,
			scope: 'history',
			groups: groups(),
			onToggle: vi.fn(),
			onClose: vi.fn(),
			onPick: vi.fn(),
			...props
		}
	});
	flushSync();
	cleanup = () => {
		unmount(instance);
		target.remove();
	};
	return target;
}

function menu(): HTMLElement {
	return document.body.querySelector('[data-tools-menu]') as HTMLElement;
}

function anchorAt(top: number, bottom: number) {
	vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
		const isTrigger = this.hasAttribute('data-tools-menu-scope') && !this.hasAttribute('data-tools-menu');
		return {
			top: isTrigger ? top : 0,
			bottom: isTrigger ? bottom : 0,
			left: 100,
			right: 180,
			width: 80,
			height: isTrigger ? bottom - top : 0,
			x: 100,
			y: top,
			toJSON: () => ({})
		} as DOMRect;
	});
}

describe('ToolsMenu', () => {
	it('draws no menu while closed and a trigger that reports its state', () => {
		const target = render({ open: false });
		expect(menu()).toBeNull();
		expect(target.querySelector('[data-tools-trigger]')!.getAttribute('aria-expanded')).toBe('false');
	});

	it('lists each group under its category with the tools in it', () => {
		render({});
		const group = menu().querySelector('[role="group"]')!;
		expect(group.getAttribute('aria-label')).toBe('Edit');
		expect(Array.from(menu().querySelectorAll('[data-tool]')).map((el) => el.getAttribute('data-tool'))).toEqual([
			'crop',
			'clear'
		]);
	});

	it('closes and reports the pick for an available tool', () => {
		const props = { onClose: vi.fn(), onPick: vi.fn() };
		render(props);
		(menu().querySelector('[data-tool="crop"]') as HTMLButtonElement).click();
		expect(props.onClose).toHaveBeenCalledTimes(1);
		expect(props.onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 'crop' }));
	});

	it('keeps a greyed tool focusable and does nothing when it is pressed', () => {
		const props = { onClose: vi.fn(), onPick: vi.fn() };
		render(props);
		const clear = menu().querySelector('[data-tool="clear"]') as HTMLButtonElement;
		expect(clear.getAttribute('aria-disabled')).toBe('true');
		expect(clear.disabled).toBe(false);
		clear.click();
		expect(props.onPick).not.toHaveBeenCalled();
		expect(props.onClose).not.toHaveBeenCalled();
	});

	it('shows the shortcut of a field tool and the reason a greyed one is unavailable', () => {
		render({ scope: 'field' });
		expect(menu().querySelector('[data-tool="crop"] kbd')?.textContent?.trim()).toBe('C');
		expect(menu().querySelector('[data-tool="clear"]')!.textContent).toContain('No mask');
	});

	it('never puts a native title on a menu item', () => {
		render({});
		expect(menu().querySelectorAll('[title]')).toHaveLength(0);
	});

	it('closes on Escape and on a pointer press outside it', () => {
		const props = { onClose: vi.fn() };
		render(props);
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
		expect(props.onClose).toHaveBeenCalledTimes(1);
		document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
		expect(props.onClose).toHaveBeenCalledTimes(2);
	});

	it('moves focus through the items with the arrow keys', () => {
		render({});
		const [first, second] = Array.from(menu().querySelectorAll<HTMLButtonElement>('[role="menuitem"]'));
		first.focus();
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', cancelable: true }));
		expect(document.activeElement).toBe(second);
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', cancelable: true }));
		expect(document.activeElement).toBe(first);
	});

	it('says there is nothing to offer when no group applies', () => {
		render({ groups: [] });
		expect(menu().textContent).toContain('No tools for this selection');
	});

	it('opens upward by default, as it does above the History bar', () => {
		anchorAt(400, 430);
		render({});
		expect(menu().style.bottom).toBe(`${window.innerHeight - 400 + 4}px`);
		expect(menu().style.top).toBe('');
	});

	it('opens downward when asked and there is room', () => {
		anchorAt(100, 130);
		render({ placement: 'down' });
		expect(menu().style.top).toBe('134px');
		expect(menu().style.bottom).toBe('');
	});

	it('flips upward when it was asked to open downward and the room is below too small', () => {
		anchorAt(700, 730);
		render({ placement: 'down' });
		expect(menu().style.bottom).not.toBe('');
	});

	it('shows an icon-only trigger with an accessible name when compact', () => {
		const target = render({ open: false, compact: true });
		const trigger = target.querySelector('[data-tools-trigger]')!;
		expect(trigger.getAttribute('aria-label')).toBe('Tools');
		expect(trigger.textContent?.trim()).toBe('');
	});
});
