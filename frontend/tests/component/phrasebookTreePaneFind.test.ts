// @vitest-environment jsdom
//
// Typing in the phrasebook tree pane's search switches it from the tree to a
// grouped results list. Clicking a value hit selects its category AND the
// value and opens the detail form in value edit mode; the row's Edit action
// saves a new value text in place through the store.
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { mount, unmount, flushSync } from 'svelte';

vi.mock('$lib/services/api/index', () => ({
	api: {
		findPhrasebook: vi.fn(),
		getPhrasebookCategories: vi.fn(),
		getPhrasebookCategory: vi.fn(),
		getCategoryChildren: vi.fn(),
		updatePhrasebookValue: vi.fn(),
		getFileURL: vi.fn(() => '')
	}
}));

const { api } = await import('$lib/services/api/index');
const { phrasebookStore } = await import('$lib/stores/phrasebook');
const { default: CategoryTreePane } = await import(
	'../../src/routes/phrasebook/components/CategoryTreePane.svelte'
);

const CATEGORY = {
	id: 'cat-1',
	name: 'Dogs',
	path: 'animals.dogs',
	parent_id: null,
	description: '',
	is_active: false,
	created_at: '',
	updated_at: ''
};

const VALUE = {
	id: 'val-1',
	category_id: 'cat-1',
	label: 'Puppy',
	value: 'a small dog',
	sort_order: 3,
	is_active: true,
	created_at: '',
	updated_at: ''
};

const HIT = { ...VALUE, category_path: 'animals.dogs', category_name: 'Dogs', category_is_active: false };

function flush(ms = 0) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount>;

function mountPane() {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(CategoryTreePane, { target, props: { width: 280 } });
}

async function typeSearch(text: string) {
	const input = target.querySelector<HTMLInputElement>('input[type="text"]');
	if (!input) throw new Error('search input missing');
	input.value = text;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	await flush(260);
	flushSync();
	await flush(0);
	flushSync();
}

beforeEach(() => {
	phrasebookStore.reset();
	vi.mocked(api.findPhrasebook).mockResolvedValue({
		success: true,
		data: { query: 'dog', categories: [CATEGORY], values: [HIT], total_categories: 1, total_values: 1 }
	} as never);
	vi.mocked(api.getPhrasebookCategory).mockResolvedValue({
		success: true,
		data: { category: CATEGORY, values: [VALUE] }
	} as never);
	vi.mocked(api.updatePhrasebookValue).mockResolvedValue({ success: true, data: {} } as never);
});

afterEach(() => {
	if (component) unmount(component);
	target?.remove();
	vi.clearAllMocks();
});

describe('CategoryTreePane search', () => {
	it('shows grouped, highlighted hits with inactive badges once a query is typed', async () => {
		mountPane();
		await typeSearch('dog');

		expect(api.findPhrasebook).toHaveBeenCalledWith('dog');
		const text = target.textContent ?? '';
		expect(text).toContain('Categories');
		expect(text).toContain('Values');
		expect(text).toContain('Puppy');
		expect(target.querySelectorAll('mark').length).toBeGreaterThan(0);
		expect(text).toContain('inactive');
		expect(target.querySelector('[role="listbox"]')).not.toBeNull();
	});

	it('selects the category and the value and opens value edit mode when a value hit is clicked', async () => {
		mountPane();
		await typeSearch('dog');

		const rows = Array.from(target.querySelectorAll<HTMLElement>('[data-pane-row]'));
		const valueRow = rows.find((r) => r.textContent?.includes('Puppy'));
		if (!valueRow) throw new Error('value row missing');
		valueRow.click();
		await flush(0);
		flushSync();

		const state = get(phrasebookStore);
		expect(state.selectedCategoryId).toBe('cat-1');
		expect(state.selectedValueId).toBe('val-1');
		expect(state.editMode).toBe('value');
		expect(state.valueForm.value).toBe('a small dog');
	});

	it('quick-edits the value text in place, saving only the changed text', async () => {
		mountPane();
		await typeSearch('dog');

		const edit = target.querySelector<HTMLButtonElement>('button[aria-label="Edit value"]');
		if (!edit) throw new Error('edit action missing');
		edit.click();
		flushSync();
		await flush(0);
		flushSync();

		const input = target.querySelector<HTMLInputElement>('[data-quick-edit] input');
		if (!input) throw new Error('quick edit input missing');
		input.value = 'a tiny dog';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		await flush(0);
		flushSync();

		expect(api.updatePhrasebookValue).toHaveBeenCalledWith('val-1', {
			category_id: 'cat-1',
			label: 'Puppy',
			value: 'a tiny dog',
			sort_order: 3
		});
		expect(target.querySelector('[data-quick-edit]')).toBeNull();
		expect(target.textContent).toContain('a tiny dog');
		expect(get(phrasebookStore).selectedValueId).toBeNull();
	});

	it('returns to the tree and shows Nothing matches for an empty result', async () => {
		vi.mocked(api.findPhrasebook).mockResolvedValue({
			success: true,
			data: { query: 'zzz', categories: [], values: [], total_categories: 0, total_values: 0 }
		} as never);
		mountPane();
		await typeSearch('zzz');
		expect(target.textContent).toContain('Nothing matches');

		await typeSearch('');
		expect(target.querySelector('[role="tree"]')).not.toBeNull();
	});
});
