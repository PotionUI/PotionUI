import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const { default: IconButton } = await import('../../src/lib/components/ui/IconButton.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function mountIconButton(props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(IconButton, {
		target,
		props: { icon: 'refresh', label: 'Index models', ...props }
	});
	flushSync();
}

function button(): HTMLButtonElement {
	return target.querySelector('button') as HTMLButtonElement;
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

describe('IconButton loading state', () => {
	it('shows a spinner in place of the icon when loading', () => {
		mountIconButton({ loading: true });
		expect(button().querySelector('.animate-spin')).not.toBeNull();
		expect(button().querySelector('svg')).toBeNull();
	});

	it('shows the icon and no spinner when not loading', () => {
		mountIconButton({ loading: false });
		expect(button().querySelector('.animate-spin')).toBeNull();
		expect(button().querySelector('svg')).not.toBeNull();
	});

	it('disables the button and sets aria-busy while loading', () => {
		mountIconButton({ loading: true });
		expect(button().disabled).toBe(true);
		expect(button().getAttribute('aria-busy')).toBe('true');
	});

	it('keeps the button enabled and aria-busy false when idle', () => {
		mountIconButton({ loading: false });
		expect(button().disabled).toBe(false);
		expect(button().getAttribute('aria-busy')).toBe('false');
	});

	it('stays disabled when both disabled and loading are set', () => {
		mountIconButton({ loading: true, disabled: true });
		expect(button().disabled).toBe(true);
	});
});
