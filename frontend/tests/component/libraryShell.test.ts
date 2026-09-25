import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync, createRawSnippet } from 'svelte';

const { default: LibraryShell } = await import('../../src/lib/components/library/LibraryShell.svelte');

const SECTIONS = [
	{ id: 'prompts', label: 'Prompts', icon: 'document' },
	{ id: 'segments', label: 'Segments', icon: 'list' },
	{ id: 'templates', label: 'Templates', icon: 'layout-template' },
	{ id: 'categories', label: 'Categories', icon: 'folder' }
] as const;

const children = createRawSnippet(() => ({
	render: () => `<div data-testid="body">body</div>`
}));

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function mountShell(overrides: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(LibraryShell, {
		target,
		props: {
			title: 'Prompt Library',
			persistKey: 'prompt-library-test',
			sections: SECTIONS,
			section: 'prompts',
			onSelectSection: vi.fn(),
			count: 12,
			children,
			...overrides
		}
	});
	flushSync();
}

function sectionRows(): HTMLElement[] {
	return Array.from(target.querySelectorAll('[aria-label="Prompt Library sections"] [role="option"]'));
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

describe('LibraryShell', () => {
	it('renders the four section rows with counts from the store and selects the active one', () => {
		mountShell({ section: 'segments', sectionCounts: { prompts: 12, segments: 7, templates: 3, categories: 2 } });
		const rows = sectionRows();
		expect(rows.map((row) => row.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
			'Prompts 12',
			'Segments 7',
			'Templates 3',
			'Categories 2'
		]);
		expect(rows.map((row) => row.getAttribute('aria-selected'))).toEqual(['false', 'true', 'false', 'false']);
	});

	it.each(['categories', 'templates'] as const)('marks only the %s row selected with the signal border', (section) => {
		mountShell({ section, sectionCounts: { prompts: 12, segments: 7, templates: 3, categories: 2 } });
		const rows = sectionRows();
		const selected = rows.filter((row) => row.getAttribute('aria-selected') === 'true');
		expect(selected).toHaveLength(1);
		expect(selected[0].textContent?.replace(/\s+/g, ' ').trim().toLowerCase()).toContain(section);
		const classes = selected[0].className.split(/\s+/);
		expect(classes).toContain('border-l-2');
		expect(classes).toContain('border-l-signal');
		expect(classes.filter((cls) => /(^|:)border-none$/.test(cls))).toEqual([]);
		expect(selected[0].getAttribute('style')).toContain('--signal');
	});

	it('places the sidebar beside the header column so it spans the full page height', () => {
		mountShell({ section: 'prompts' });
		const aside = target.querySelector('aside') as HTMLElement;
		const heading = target.querySelector('h1') as HTMLElement;
		expect(aside.parentElement?.className).toContain('h-[100dvh]');
		expect(aside.className).toContain('w-60');
		expect(aside.contains(heading)).toBe(false);
		expect(aside.nextElementSibling?.contains(heading)).toBe(true);
		expect(aside.querySelector('[aria-label="Prompt Library sections"]')).not.toBeNull();
	});

	it('renders the sidebar label inside the Pane header at the 12px floor', () => {
		mountShell({ section: 'prompts' });
		const aside = target.querySelector('aside') as HTMLElement;
		const header = aside.querySelector('.min-h-header') as HTMLElement;
		const label = Array.from(header.querySelectorAll('span')).find((el) => el.textContent?.trim() === 'Prompt Library') as HTMLElement;
		const classes = label.className.split(/\s+/);
		expect(classes).toEqual(expect.arrayContaining(['font-mono', 'text-xs', 'uppercase']));
		expect(header.querySelector('button[aria-label="Collapse"]')).not.toBeNull();
	});

	it('collapses to a narrow rail with one button per section and restores from it', () => {
		mountShell({ section: 'templates' });
		(target.querySelector('button[aria-label="Collapse"]') as HTMLButtonElement).click();
		flushSync();
		const rail = target.querySelector('aside') as HTMLElement;
		expect(rail.className).toContain('w-8');
		expect(rail.querySelector('[aria-label="Prompt Library sections"]')).toBeNull();
		const sectionButtons = ['Prompts', 'Segments', 'Templates', 'Categories'].map(
			(label) => rail.querySelector(`button[aria-label="${label}"]`) as HTMLButtonElement
		);
		expect(sectionButtons.every(Boolean)).toBe(true);
		expect(sectionButtons.map((button) => button.getAttribute('aria-current'))).toEqual([null, null, 'page', null]);
		(rail.querySelector('button[aria-label="Show sidebar"]') as HTMLButtonElement).click();
		flushSync();
		expect(target.querySelector('aside')?.className).toContain('w-60');
	});

	it('renders the toolbar slot, overflow menu and the primary action in grid mode', () => {
		const toolbar = createRawSnippet(() => ({ render: () => `<div data-testid="toolbar">toolbar</div>` }));
		const primary = createRawSnippet(() => ({ render: () => `<button type="button">New prompt</button>` }));
		const overflow = createRawSnippet(() => ({ render: () => `<button type="button" role="menuitem">Export</button>` }));
		mountShell({ toolbar, overflow, primary });
		expect(target.querySelector('[data-testid="toolbar"]')).not.toBeNull();
		expect(target.querySelector('button[aria-label="More actions"]')).not.toBeNull();
		expect(buttonByText('New prompt')).toBeTruthy();
		expect(target.querySelector('h1')?.parentElement?.parentElement?.textContent).toContain('12');
	});

	it('places the overflow menu button to the right of the primary action', () => {
		const toolbar = createRawSnippet(() => ({ render: () => `<div data-testid="toolbar">toolbar</div>` }));
		const primary = createRawSnippet(() => ({ render: () => `<button type="button">New prompt</button>` }));
		const overflow = createRawSnippet(() => ({ render: () => `<button type="button" role="menuitem">Export</button>` }));
		mountShell({ toolbar, overflow, primary });
		const primaryButton = buttonByText('New prompt')!;
		const moreButton = target.querySelector('button[aria-label="More actions"]')!;
		expect(primaryButton.compareDocumentPosition(moreButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it('hides the toolbar, primary action and overflow while the detail view is open', () => {
		const toolbar = createRawSnippet(() => ({ render: () => `<div data-testid="toolbar">toolbar</div>` }));
		const primary = createRawSnippet(() => ({ render: () => `<button type="button">New prompt</button>` }));
		mountShell({ toolbar, primary, detailOpen: true });
		expect(target.querySelector('[data-testid="toolbar"]')).toBeNull();
		expect(buttonByText('New prompt')).toBeUndefined();
		expect(target.querySelector('button[aria-label="More actions"]')).toBeNull();
		expect(target.querySelector('[data-testid="body"]')).not.toBeNull();
	});

	it('does not render its own PageTitle row while the detail view is open, leaving only the body', () => {
		mountShell({ detailOpen: true });
		const main = target.querySelector('main') as HTMLElement;
		expect(main.previousElementSibling).toBeNull();
		expect(target.querySelector('h1')).toBeNull();
		expect(main.textContent).not.toContain('Prompt Library');
	});

	it('renders its PageTitle row above the body when the detail view is closed', () => {
		mountShell({ detailOpen: false });
		const main = target.querySelector('main') as HTMLElement;
		expect(main.previousElementSibling).not.toBeNull();
		const heading = target.querySelector('h1')?.textContent ?? '';
		expect(heading).toContain('Prompts');
		expect(heading).not.toContain('Prompt Library');
	});

	it('lets titleLabel override the PageTitle text without changing which section row is selected', () => {
		mountShell({ section: 'prompts', titleLabel: 'LoRA', sectionCounts: { prompts: 12, segments: 7, templates: 3, categories: 2 } });
		const heading = target.querySelector('h1')?.textContent ?? '';
		expect(heading).toContain('LoRA');
		expect(heading).not.toContain('Prompts');
		const rows = sectionRows();
		const selected = rows.filter((row) => row.getAttribute('aria-selected') === 'true');
		expect(selected).toHaveLength(1);
		expect(selected[0].textContent).toContain('Prompts');
	});

	it('falls back to the section label when titleLabel is not passed', () => {
		mountShell({ section: 'segments' });
		const heading = target.querySelector('h1')?.textContent ?? '';
		expect(heading).toContain('Segments');
	});

	it('renders the chip row with N of M only while chips exist', () => {
		mountShell({ filterChips: [], loadedCount: 5, total: 40 });
		expect(target.textContent).not.toContain('5 of 40');
		expect(buttonByText('Clear all')).toBeUndefined();
		unmount(component!);
		component = null;
		target.remove();

		const onRemoveChip = vi.fn();
		mountShell({ filterChips: [{ key: 'tag:dance', label: '#dance' }], loadedCount: 5, total: 40, onRemoveChip });
		expect(target.textContent).toContain('#dance');
		expect(target.textContent).toContain('5 of 40');
		expect(buttonByText('Clear all')).toBeTruthy();
		(target.querySelector('button[aria-label="Remove filter #dance"]') as HTMLButtonElement).click();
		expect(onRemoveChip).toHaveBeenCalledWith('tag:dance');
	});
	it('renders no sidebar when there is a single section and no sidebar tree', () => {
		mountShell({ sections: [SECTIONS[0]], section: 'prompts' });
		expect(target.querySelector('aside')).toBeNull();
	});
});
