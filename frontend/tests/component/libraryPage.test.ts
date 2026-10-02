// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import { getRegistry, registerComponent } from '../../src/lib/plugin-api/componentRegistry';

const { default: LibraryPage } = await import('../../src/lib/components/library/LibraryPage.svelte');

const SECTIONS = [
	{ id: 'all', label: 'All', icon: 'list' },
	{ id: 'done', label: 'Done', icon: 'check' }
];

let target: HTMLDivElement;
let handle: unknown;

function mountThroughHost(props: Record<string, unknown>) {
	registerComponent('LibraryPage', LibraryPage);
	const entry = getRegistry().LibraryPage;
	target = document.createElement('div');
	document.body.appendChild(target);
	handle = entry.mount(target, {
		title: 'Things',
		persistKey: 'library-page-test',
		sections: SECTIONS,
		section: 'all',
		onSelectSection: vi.fn(),
		children: (el: HTMLElement) => {
			el.innerHTML = '<p data-testid="body">hosted body</p>';
		},
		...props
	});
	flushSync();
	return entry;
}

afterEach(() => {
	if (handle) getRegistry().LibraryPage.unmount(handle);
	handle = null;
	target?.remove();
});

describe('LibraryPage mounted through the host registry', () => {
	it('renders the title, count and the projected body from plain props', () => {
		mountThroughHost({ count: 4, titleLabel: 'Things' });
		expect(target.querySelector('h1')?.textContent).toBe('Things');
		expect(target.textContent).toContain('4');
		expect(target.querySelector('[data-testid="body"]')?.textContent).toBe('hosted body');
	});

	it('lists the sections with their counts and reports a selection', () => {
		const onSelectSection = vi.fn();
		mountThroughHost({ sectionCounts: { all: 5, done: 2 }, onSelectSection });
		const rows = Array.from(target.querySelectorAll<HTMLElement>('[aria-label="Things sections"] [role="option"]'));
		expect(rows.map((row) => row.textContent?.replace(/\s+/g, ' ').trim())).toEqual(['All 5', 'Done 2']);
		rows[1].click();
		expect(onSelectSection).toHaveBeenCalledWith('done');
	});

	it('reports typed search text', () => {
		const onQueryChange = vi.fn();
		mountThroughHost({ searchPlaceholder: 'Search things', onQueryChange });
		const input = target.querySelector<HTMLInputElement>('input[type="search"]')!;
		expect(input.placeholder).toBe('Search things');
		input.value = 'abc';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		expect(onQueryChange).toHaveBeenCalledWith('abc');
	});

	it('shows the primary action and calls it', () => {
		const onPrimary = vi.fn();
		mountThroughHost({ primaryLabel: 'New thing', primaryIcon: 'plus', onPrimary });
		const button = Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('New thing'))!;
		button.click();
		expect(onPrimary).toHaveBeenCalledTimes(1);
	});

	it('omits the primary action without a label', () => {
		mountThroughHost({});
		expect(Array.from(target.querySelectorAll('button')).some((b) => b.textContent?.includes('New thing'))).toBe(false);
	});

	it('follows prop updates pushed through the host entry', () => {
		const entry = mountThroughHost({ count: 1, section: 'all' });
		entry.update(handle, { count: 9, section: 'done' });
		flushSync();
		expect(target.textContent).toContain('9');
		const selected = target.querySelector('[aria-label="Things sections"] [aria-selected="true"]');
		expect(selected?.textContent).toContain('Done');
	});
});
