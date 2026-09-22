import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync, createRawSnippet } from 'svelte';

const { default: PageTitle } = await import('../../src/lib/components/ui/PageTitle.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function mountTitle(props: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(PageTitle, { target, props: { title: 'History', ...props } });
	flushSync();
}

function eyebrow(): HTMLElement | null {
	return target.querySelector('span.font-mono');
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

describe('PageTitle', () => {
	it('renders the title as an h1 and the count with its label as the eyebrow', () => {
		mountTitle({ count: 214, countLabel: 'generations' });
		const heading = target.querySelector('h1') as HTMLElement;
		expect(heading.textContent).toBe('History');
		expect(heading.className.split(/\s+/)).toEqual(expect.arrayContaining(['text-sm', 'font-semibold', 'truncate']));
		const label = eyebrow() as HTMLElement;
		expect(label.textContent?.replace(/\s+/g, ' ').trim()).toBe('214 generations');
		expect(label.className.split(/\s+/)).toEqual(expect.arrayContaining(['text-xs', 'tabular-nums', 'uppercase']));
		expect(label.className).not.toContain('text-2xs');
	});

	it('renders a bare count when no label is given and a string count verbatim', () => {
		mountTitle({ count: 0 });
		expect(eyebrow()?.textContent?.trim()).toBe('0');
		unmount(component!);
		component = null;
		target.remove();
		mountTitle({ count: 'Manage your account' });
		expect(eyebrow()?.textContent?.trim()).toBe('Manage your account');
	});

	it('renders description as a muted line under the title, never as the eyebrow', () => {
		mountTitle({ description: 'Manage your account and application settings' });
		expect(eyebrow()).toBeNull();
		const line = target.querySelector('p') as HTMLElement;
		expect(line.textContent).toBe('Manage your account and application settings');
		expect(line.previousElementSibling?.tagName).toBe('H1');
		const classes = line.className.split(/\s+/);
		expect(classes).toEqual(expect.arrayContaining(['text-xs', 'text-fg-muted', 'truncate', 'hidden', 'sm:block']));
		expect(classes).not.toEqual(expect.arrayContaining(['font-mono']));
		expect(classes).not.toEqual(expect.arrayContaining(['uppercase']));
	});

	it('omits the eyebrow without a count', () => {
		mountTitle({ countLabel: 'items' });
		expect(eyebrow()).toBeNull();
		expect(target.textContent?.trim()).toBe('History');
	});

	it('renders the leading snippet before the title and children after the eyebrow', () => {
		const leading = createRawSnippet(() => ({ render: () => `<svg data-testid="lead"></svg>` }));
		const children = createRawSnippet(() => ({ render: () => `<select data-testid="after"></select>` }));
		mountTitle({ count: 3, countLabel: 'topics', leading, children });
		const root = target.firstElementChild as HTMLElement;
		const order = Array.from(root.querySelectorAll('[data-testid], h1, span.font-mono')).map(
			(el) => el.getAttribute('data-testid') ?? el.tagName.toLowerCase()
		);
		expect(order).toEqual(['lead', 'h1', 'span', 'after']);
	});
});
