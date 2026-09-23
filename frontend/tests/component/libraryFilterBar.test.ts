import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync, createRawSnippet } from 'svelte';

const { default: LibraryFilterBar } = await import('../../src/lib/components/library/LibraryFilterBar.svelte');

const popover = createRawSnippet(() => ({
	render: () => `<div role="dialog" aria-label="Filters" data-testid="filters-popover">popover</div>`
}));

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function mountBar(overrides: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(LibraryFilterBar, {
		target,
		props: {
			q: '',
			onQueryChange: vi.fn(),
			searchPlaceholder: 'Search prompts…',
			sortBy: 'name',
			sortOptions: [
				{ value: 'name', label: 'Name' },
				{ value: 'created_at', label: 'Created' }
			],
			onSortChange: vi.fn(),
			...overrides
		}
	});
	flushSync();
}

function buttonByText(text: string): HTMLButtonElement | undefined {
	return Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.trim().startsWith(text)) as
		| HTMLButtonElement
		| undefined;
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

describe('LibraryFilterBar', () => {
	it('renders the search input, Filters button with a count badge, and the sort select', () => {
		mountBar({ popover, filterCount: 2 });
		expect(target.querySelector('input[type="search"]')).not.toBeNull();
		expect(buttonByText('Filters')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('Filters 2');
		expect(target.querySelector('select[class*="input"]')).not.toBeNull();
	});

	it('omits the count badge when no filters are active', () => {
		mountBar({ popover, filterCount: 0 });
		expect(buttonByText('Filters')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('Filters');
	});

	it('calls onQueryChange as the search input changes', () => {
		const onQueryChange = vi.fn();
		mountBar({ onQueryChange });
		const input = target.querySelector('input[type="search"]') as HTMLInputElement;
		input.value = 'dance';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		expect(onQueryChange).toHaveBeenCalledWith('dance');
	});

	it('calls onSortChange when the sort select changes', () => {
		const onSortChange = vi.fn();
		mountBar({ onSortChange });
		const select = target.querySelector('select') as HTMLSelectElement;
		select.value = 'created_at';
		select.dispatchEvent(new Event('change', { bubbles: true }));
		expect(onSortChange).toHaveBeenCalledWith('created_at');
	});

	it('opens the filters popover from the Filters button, portaled to <body>, and closes it on Escape', () => {
		mountBar({ popover });
		expect(document.body.querySelector('[data-testid="filters-popover"]')).toBeNull();
		buttonByText('Filters')!.click();
		flushSync();
		const panel = document.body.querySelector('[data-testid="filters-popover"]');
		expect(panel).not.toBeNull();
		expect(target.contains(panel)).toBe(false);
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();
		expect(document.body.querySelector('[data-testid="filters-popover"]')).toBeNull();
	});

	it('closes the filters popover on an outside click', () => {
		mountBar({ popover });
		buttonByText('Filters')!.click();
		flushSync();
		expect(document.body.querySelector('[data-testid="filters-popover"]')).not.toBeNull();
		document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();
		expect(document.body.querySelector('[data-testid="filters-popover"]')).toBeNull();
	});
});
