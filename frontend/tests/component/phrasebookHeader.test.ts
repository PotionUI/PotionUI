// @vitest-environment jsdom
//
// The merged phrasebook header owns the find input, the match-mode toggle and
// the Filters popover (case, fields, scope, include inactive, subtree). Find
// state itself lives on the page — this only checks the header reports
// changes correctly and the Filters popover behaves.
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

vi.mock('$lib/services/api/index', () => ({
	api: {
		importPhrasebookYAML: vi.fn(),
		getPhrasebookCategories: vi.fn(async () => ({ success: true, data: { categories: [] } })),
		getCategoryChildren: vi.fn(),
		getFileURL: vi.fn(() => ''),
		setOnAuthExpired: vi.fn(),
		getBaseURL: vi.fn(() => ''),
		getToken: vi.fn(() => null)
	}
}));

vi.mock('$lib/stores/plugins', async () => {
	const { writable } = await import('svelte/store');
	return { pluginStore: { loadFrontendHooks: vi.fn(async () => {}) }, frontendHooks: writable({}) };
});

const { api } = await import('$lib/services/api/index');
const { get } = await import('svelte/store');
const { phrasebookStore } = await import('$lib/stores/phrasebook');
const { default: PhrasebookHeader } = await import(
	'../../src/routes/phrasebook/components/PhrasebookHeader.svelte'
);
const { defaultFilters } = await import('../../src/routes/phrasebook/phrasebookSearch');

function flush(ms = 0) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

async function settle() {
	flushSync();
	await flush(0);
	flushSync();
}

function buttonByText(text: string, root: ParentNode = document): HTMLButtonElement {
	const button = Array.from(root.querySelectorAll<HTMLButtonElement>('button')).find(
		(b) => b.textContent?.trim() === text
	);
	if (!button) throw new Error(`missing button ${text}`);
	return button;
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount>;
let onChange: ReturnType<typeof vi.fn>;
let onClear: ReturnType<typeof vi.fn>;

function mountHeader(filters = defaultFilters(), extra: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	onChange = vi.fn();
	onClear = vi.fn();
	component = mount(PhrasebookHeader, {
		target,
		props: { filters, searching: false, error: null, topLevel: [], onChange, onClear, ...extra }
	});
	flushSync();
}

beforeEach(() => {
	phrasebookStore.reset();
});

afterEach(() => {
	if (component) unmount(component);
	target?.remove();
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('PhrasebookHeader', () => {
	it('reports typing in the find input through onChange', () => {
		mountHeader();
		const input = target.querySelector<HTMLInputElement>('[data-find-input]');
		if (!input) throw new Error('missing find input');
		input.value = 'dog';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(onChange).toHaveBeenCalledWith({ query: 'dog' });
	});

	it('shows a clear button once a query is active and calls onClear', () => {
		mountHeader({ ...defaultFilters(), query: 'dog' });
		const clearButton = target.querySelector<HTMLButtonElement>('[aria-label="Clear search"]');
		if (!clearButton) throw new Error('missing clear button');
		clearButton.click();
		flushSync();
		expect(onClear).toHaveBeenCalled();
	});

	it('shows no Filters badge for default filters', () => {
		mountHeader();
		expect(target.querySelector('[data-filters-count]')).toBeNull();
	});

	it('shows a count of 1 for a single filter deviation', () => {
		mountHeader({ ...defaultFilters(), caseSensitive: true });
		expect(target.querySelector('[data-filters-count]')?.textContent?.trim()).toBe('1');
	});

	it('opens the Filters popover from the trigger and closes it on outside click', () => {
		mountHeader();
		const trigger = target.querySelector<HTMLButtonElement>('[data-filters-trigger]');
		if (!trigger) throw new Error('missing filters trigger');

		trigger.click();
		flushSync();
		expect(document.querySelector('[data-filters-popover]')).not.toBeNull();
		expect(trigger.getAttribute('aria-expanded')).toBe('true');

		document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
		flushSync();
		expect(document.querySelector('[data-filters-popover]')).toBeNull();
	});

	it('Reset filters clears every non-default filter back to defaults', () => {
		mountHeader({ ...defaultFilters(), caseSensitive: true, scope: 'categories', pathPrefix: 'animals' });
		target.querySelector<HTMLButtonElement>('[data-filters-trigger]')?.click();
		flushSync();

		const resetButton = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
			b.textContent?.trim().startsWith('Reset filters')
		);
		if (!resetButton) throw new Error('missing Reset filters button');
		resetButton.click();
		flushSync();

		expect(onChange).toHaveBeenCalledWith({
			caseSensitive: false,
			inLabel: true,
			inValue: true,
			scope: 'all',
			includeInactive: true,
			pathPrefix: ''
		});
	});

	it('picking Show = Active in the Filters popover adds 1 to the badge', async () => {
		mountHeader();
		target.querySelector<HTMLButtonElement>('[data-filters-trigger]')?.click();
		flushSync();

		const showGroup = document.querySelector('[role="group"][aria-label="Show"]');
		if (!showGroup) throw new Error('missing Show group');
		buttonByText('Active', showGroup).click();
		await settle();

		expect(get(phrasebookStore).stateFilter).toBe('active');
		expect(target.querySelector('[data-filters-count]')?.textContent?.trim()).toBe('1');
	});

	it('opens the Import popover from the trigger', () => {
		mountHeader();
		expect(document.querySelector('[data-import-popover]')).toBeNull();

		const trigger = target.querySelector<HTMLButtonElement>('[data-import-trigger]');
		if (!trigger) throw new Error('missing import trigger');
		trigger.click();
		flushSync();

		expect(document.querySelector('[data-import-popover]')).not.toBeNull();
		expect(trigger.getAttribute('aria-expanded')).toBe('true');
	});

	it('imports the chosen file with the root category field through importPhrasebookYAML', async () => {
		vi.mocked(api.importPhrasebookYAML).mockResolvedValue({
			success: true,
			data: { categories_created: 1, values_created: 3 }
		} as never);

		mountHeader();
		target.querySelector<HTMLButtonElement>('[data-import-trigger]')?.click();
		flushSync();

		const fileInput = document.querySelector<HTMLInputElement>('[data-import-file-input]');
		if (!fileInput) throw new Error('missing import file input');
		const file = new File(['a: 1'], 'animals.yaml', { type: 'application/x-yaml' });
		Object.defineProperty(fileInput, 'files', { value: [file], configurable: true });
		fileInput.dispatchEvent(new Event('change', { bubbles: true }));
		flushSync();

		expect(document.querySelector('[data-import-file-name]')?.textContent).toContain('animals.yaml');
		expect(document.querySelector('[data-import-file-error]')).toBeNull();

		const rootInput = document.querySelector<HTMLInputElement>('#phrasebook-import-root');
		if (!rootInput) throw new Error('missing root category input');
		rootInput.value = 'Imported';
		rootInput.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();

		buttonByText('Import', document.querySelector('[data-import-popover]')!).click();
		await settle();

		expect(api.importPhrasebookYAML).toHaveBeenCalledWith(file, 'Imported');
		expect(document.querySelector('[data-import-popover]')).toBeNull();
	});

	it('rejects a non-YAML file with an inline error and leaves the confirm button disabled', () => {
		mountHeader();
		target.querySelector<HTMLButtonElement>('[data-import-trigger]')?.click();
		flushSync();

		const fileInput = document.querySelector<HTMLInputElement>('[data-import-file-input]');
		if (!fileInput) throw new Error('missing import file input');
		const file = new File(['x'], 'notes.txt', { type: 'text/plain' });
		Object.defineProperty(fileInput, 'files', { value: [file], configurable: true });
		fileInput.dispatchEvent(new Event('change', { bubbles: true }));
		flushSync();

		expect(document.querySelector('[data-import-file-error]')?.textContent).toContain('YAML file');
		const importButton = buttonByText('Import', document.querySelector('[data-import-popover]')!);
		expect(importButton.disabled).toBe(true);
	});
});
