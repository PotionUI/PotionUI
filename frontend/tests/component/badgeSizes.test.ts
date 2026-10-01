// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, createRawSnippet } from 'svelte';
import Badge from '$lib/components/ui/Badge.svelte';

let cleanup: (() => void) | undefined;

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
});

function render(size?: 'sm' | 'md' | 'lg') {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const children = createRawSnippet(() => ({ render: () => '<span>Done</span>' }));
	const instance = mount(Badge, { target, props: { size, dot: true, variant: 'success', children } });
	cleanup = () => {
		unmount(instance);
		target.remove();
	};
	return target.firstElementChild as HTMLElement;
}

describe('Badge sizes', () => {
	it('has a 12 px size for labels that must stay readable', () => {
		const badge = render('lg');

		expect(badge.className).toContain('text-sm');
		expect(badge.className).not.toContain('text-xs');
		expect(badge.textContent?.trim()).toBe('Done');
	});

	it('keeps the existing sizes as they were', () => {
		expect(render('md').className).toContain('text-xs');
		cleanup?.();
		expect(render().className).toContain('text-xs');
		cleanup?.();
		expect(render('sm').className).toContain('text-2xs');
	});
});
