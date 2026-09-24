import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync, createRawSnippet } from 'svelte';

const { default: DetailLayout } = await import('../../src/lib/components/detail/DetailLayout.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

const leadSnippet = createRawSnippet(() => ({ render: () => `<p data-testid="lead">lead</p>` }));
const mainSnippet = createRawSnippet(() => ({ render: () => `<p data-testid="main">main</p>` }));
const asideSnippet = createRawSnippet(() => ({ render: () => `<p data-testid="aside">aside</p>` }));

function mountLayout(props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(DetailLayout, { target, props: { main: mainSnippet, ...props } });
	flushSync();
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

describe('DetailLayout', () => {
	it('renders main only, with the main-only modifier class, when no aside is given', () => {
		mountLayout();
		expect(target.querySelector('[data-testid="main"]')).not.toBeNull();
		expect(target.querySelector('[data-testid="aside"]')).toBeNull();
		const columns = target.querySelector('.detail-layout-columns') as HTMLElement;
		expect(columns.className.split(/\s+/)).toContain('detail-layout-columns--main-only');
	});

	it('renders main and aside side by side in the DOM when both are given', () => {
		mountLayout({ aside: asideSnippet });
		expect(target.querySelector('[data-testid="main"]')).not.toBeNull();
		expect(target.querySelector('[data-testid="aside"]')).not.toBeNull();
		const columns = target.querySelector('.detail-layout-columns') as HTMLElement;
		expect(columns.className).not.toContain('detail-layout-columns--main-only');
		expect(target.querySelector('[data-detail-layout-aside]')).not.toBeNull();
	});

	it('renders lead above the columns only when given', () => {
		mountLayout();
		expect(target.querySelector('.detail-layout-lead')).toBeNull();

		mountLayout({ lead: leadSnippet });
		expect(target.querySelector('.detail-layout-lead [data-testid="lead"]')).not.toBeNull();
	});
});
