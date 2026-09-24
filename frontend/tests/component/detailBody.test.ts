import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync, createRawSnippet } from 'svelte';

const { default: DetailBody } = await import('../../src/lib/components/detail/DetailBody.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

const childrenSnippet = createRawSnippet(() => ({
	render: () => `<p data-testid="body-child">content</p>`
}));

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

describe('DetailBody', () => {
	it('renders children directly, with no width cap on the scroll container', () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		component = mount(DetailBody, { target, props: { children: childrenSnippet } });
		flushSync();

		const scrollContainer = target.firstElementChild as HTMLElement;
		expect(scrollContainer.className).not.toContain('max-w-2xl');
		expect(scrollContainer.querySelector('[data-testid="body-child"]')).not.toBeNull();
	});
});
