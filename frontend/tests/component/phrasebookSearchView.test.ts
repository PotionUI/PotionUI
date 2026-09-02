// @vitest-environment jsdom
//
// The phrasebook search view renders find hits with span highlights, owns the
// row selection, and drives every batch operation (replace preview + apply,
// activate/deactivate) through the batch endpoint. A category hit hands the
// user back to the tree on that category.
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { mount, unmount, flushSync } from 'svelte';

vi.mock('$lib/services/api/index', () => ({
	api: {
		findPhrasebook: vi.fn(),
		listPhrasebookBatchOps: vi.fn(),
		runPhrasebookBatch: vi.fn(),
		previewPhrasebookBatch: vi.fn(),
		getPhrasebookCategories: vi.fn(),
		getPhrasebookCategory: vi.fn(),
		getCategoryChildren: vi.fn(),
		updatePhrasebookValue: vi.fn(),
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
const { phrasebookStore } = await import('$lib/stores/phrasebook');
const { toasts } = await import('$lib/stores/toast');
const { default: PhrasebookSearchView } = await import(
	'../../src/routes/phrasebook/components/PhrasebookSearchView.svelte'
);
const { defaultFilters } = await import('../../src/routes/phrasebook/phrasebookSearch');

const CATEGORY = {
	id: 'cat-1',
	name: 'Dogs',
	path: 'animals.dogs',
	parent_id: undefined,
	description: '',
	is_active: false,
	created_at: '',
	updated_at: '',
	matches: [{ field: 'name', start: 0, end: 3 }]
};

function hit(id: string, label: string, value: string, active = true) {
	return {
		id,
		category_id: 'cat-1',
		label,
		value,
		sort_order: 0,
		is_active: active,
		created_at: '',
		updated_at: '',
		category_path: 'animals.dogs',
		category_name: 'Dogs',
		category_is_active: false,
		matches: [{ field: 'value', start: value.toLowerCase().indexOf('dog'), end: value.toLowerCase().indexOf('dog') + 3 }]
	};
}

const RESULT = {
	query: 'dog',
	mode: 'contains' as const,
	case_sensitive: false,
	scope: 'all' as const,
	categories: [CATEGORY],
	values: [hit('v1', 'Puppy', 'a small dog'), hit('v2', 'Hound', 'a hunting DOG', false)],
	total_categories: 1,
	total_values: 2
};

function flush(ms = 0) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

async function settle() {
	flushSync();
	await flush(0);
	flushSync();
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount>;
let onClearQuery: ReturnType<typeof vi.fn>;
let onRerun: ReturnType<typeof vi.fn>;

function mountView(filters = { ...defaultFilters(), query: 'dog' }) {
	target = document.createElement('div');
	document.body.appendChild(target);
	onClearQuery = vi.fn();
	onRerun = vi.fn();
	component = mount(PhrasebookSearchView, {
		target,
		props: { result: RESULT, loading: false, filters, onClearQuery, onRerun }
	});
	flushSync();
}

function click(selector: string, root: ParentNode = document) {
	const el = root.querySelector<HTMLElement>(selector);
	if (!el) throw new Error(`missing ${selector}`);
	el.click();
	flushSync();
	return el;
}

function buttonByText(text: string, root: ParentNode = document): HTMLButtonElement {
	const button = Array.from(root.querySelectorAll<HTMLButtonElement>('button')).find(
		(b) => b.textContent?.trim() === text
	);
	if (!button) throw new Error(`missing button ${text}`);
	return button;
}

beforeEach(() => {
	if (!Element.prototype.animate) {
		Element.prototype.animate = () => {
			const animation = {
				cancel() {},
				finished: Promise.resolve(),
				onfinish: null as (() => void) | null,
				currentTime: 0,
				playbackRate: 1
			};
			setTimeout(() => animation.onfinish?.(), 0);
			return animation as never;
		};
	}
	phrasebookStore.reset();
	vi.mocked(api.getPhrasebookCategory).mockResolvedValue({
		success: true,
		data: { category: CATEGORY, values: [] }
	} as never);
	vi.mocked(api.listPhrasebookBatchOps).mockResolvedValue({
		success: true,
		data: [
			{ id: 'replace', label: 'Replace…', component: null, has_preview: true, source: 'core' },
			{ id: 'set_active', label: 'Activate / Deactivate', component: null, has_preview: false, source: 'core' },
			{ id: 'move', label: 'Move to…', component: null, has_preview: false, source: 'core' },
			{ id: 'delete', label: 'Delete', component: null, has_preview: false, source: 'core' }
		]
	} as never);
	vi.mocked(api.previewPhrasebookBatch).mockResolvedValue({
		success: true,
		data: {
			items: [{ id: 'v1', field: 'value', before: 'a small dog', after: 'a small cat' }],
			changed: 1,
			unchanged: ['v2']
		}
	} as never);
	vi.mocked(api.runPhrasebookBatch).mockImplementation(async (op: string) => {
		if (op === 'replace') {
			return {
				success: true,
				data: { updated: [{ ...RESULT.values[0], value: 'a small cat' }], skipped: ['v2'], deleted: [], message: 'Replaced in 1 value' }
			} as never;
		}
		if (op === 'set_active') {
			return { success: true, data: { updated: RESULT.values, skipped: [], deleted: [], message: 'Deactivated 1 value' } } as never;
		}
		return { success: true, data: { updated: [], skipped: [], deleted: [], message: 'Done' } } as never;
	});
});

afterEach(() => {
	if (component) unmount(component);
	target?.remove();
	document.body.innerHTML = '';
	document.body.style.overflow = '';
	vi.clearAllMocks();
});

describe('PhrasebookSearchView', () => {
	it('renders category and value hits with highlights, paths and active badges', () => {
		mountView();

		expect(target.querySelector('[data-search-counts]')?.textContent).toContain('2');
		expect(target.querySelector('[data-search-counts]')?.textContent).toContain('1');
		const marks = Array.from(target.querySelectorAll('mark')).map((m) => m.textContent);
		expect(marks).toContain('Dog');
		expect(marks).toContain('dog');
		expect(marks).toContain('DOG');
		expect(target.querySelectorAll('[data-value-row]').length).toBe(2);
		expect(target.textContent).toContain('animals.dogs');
		const hound = target.querySelector('[data-value-row="v2"]');
		expect(hound?.textContent).toContain('inactive');
		expect(target.querySelector('[data-selection-bar]')).toBeNull();
	});

	it('select-all then Replace previews and applies through the batch endpoint', async () => {
		mountView();

		click('thead input[type="checkbox"]', target);
		expect(document.querySelector('[data-selection-bar]')?.textContent).toContain('2');

		buttonByText('Replace…').click();
		await settle();
		expect(document.querySelector('[data-replace-modal]')).not.toBeNull();

		await flush(0);
		flushSync();
		expect(api.previewPhrasebookBatch).toHaveBeenCalledWith('replace', ['v1', 'v2'], {
			find: 'dog',
			replace: '',
			mode: 'contains',
			case_sensitive: false,
			fields: ['label', 'value']
		});

		const input = document.querySelector<HTMLInputElement>('[data-replace-input]');
		if (!input) throw new Error('replace input missing');
		input.value = 'cat';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await flush(260);
		await settle();

		expect(api.previewPhrasebookBatch).toHaveBeenLastCalledWith(
			'replace',
			['v1', 'v2'],
			expect.objectContaining({ replace: 'cat' })
		);
		const preview = document.querySelector('[data-replace-preview]');
		expect(preview?.textContent).toContain('cat');
		expect(document.querySelector('[data-replace-count]')?.textContent).toContain('1 will change');

		const successSpy = vi.spyOn(toasts, 'success');
		buttonByText('Apply').click();
		await settle();
		await settle();

		expect(api.runPhrasebookBatch).toHaveBeenCalledWith('replace', ['v1', 'v2'], {
			find: 'dog',
			replace: 'cat',
			mode: 'contains',
			case_sensitive: false,
			fields: ['label', 'value']
		});
		expect(successSpy).toHaveBeenCalledWith('Replaced in 1 value');
		expect(onRerun).toHaveBeenCalled();
		await flush(10);
		flushSync();
		expect(document.querySelector('[data-replace-modal]')).toBeNull();
	});

	it('Deactivate sends set_active false for the selected rows only', async () => {
		mountView();

		click('[data-value-row="v2"] input[type="checkbox"]', target);
		const successSpy = vi.spyOn(toasts, 'success');
		buttonByText('Deactivate').click();
		await settle();
		await settle();

		expect(api.runPhrasebookBatch).toHaveBeenCalledWith('set_active', ['v2'], { is_active: false });
		expect(successSpy).toHaveBeenCalledWith('Deactivated 1 value');
		expect(onRerun).toHaveBeenCalled();
	});

	it('clicking a category hit selects it in the store and clears the query', async () => {
		mountView();

		click('[data-category-hit="cat-1"]', target);
		await settle();
		await settle();

		expect(get(phrasebookStore).selectedCategoryId).toBe('cat-1');
		expect(onClearQuery).toHaveBeenCalled();
	});

	it('offers no More menu when only core ops are registered', async () => {
		mountView();
		await settle();
		click('thead input[type="checkbox"]', target);
		expect(document.querySelector('[data-batch-more]')).toBeNull();
	});

	it('hides the values table when the scope is categories only', () => {
		mountView({ ...defaultFilters(), query: 'dog', scope: 'categories' });
		expect(target.querySelector('[data-search-table]')).toBeNull();
		expect(target.querySelector('[data-search-categories]')).not.toBeNull();
	});
});
