// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const { default: PromptPickerBrowseModal } = await import('$lib/components/PromptPickerBrowseModal.svelte');
const { default: AutocompleteDropdown } = await import('$lib/components/AutocompleteDropdown.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLDivElement;
let modal: ReturnType<typeof mount> | undefined;
let dropdown: ReturnType<typeof createClassComponent> | undefined;

const values = [
	{ id: 'v1', category_id: 'c1', label: 'golden hour', value: 'warm rim light', sort_order: 0, created_at: '', updated_at: '' },
	{ id: 'v2', category_id: 'c1', label: 'blue hour', value: 'cool ambient light', sort_order: 1, created_at: '', updated_at: '' },
	{ id: 'v3', category_id: 'c1', label: 'hard noon', value: 'short shadows', sort_order: 2, created_at: '', updated_at: '', preview_file_id: 'file-3' }
];

function freshTarget() {
	target = document.createElement('div');
	document.body.appendChild(target);
	return target;
}

function mountModal(props: Record<string, unknown> = {}) {
	modal = mount(PromptPickerBrowseModal as never, {
		target: freshTarget(),
		props: {
			triggerChar: '#',
			title: 'Insert from Phrasebook',
			contextMarker: '#lighting',
			layout: 'list',
			values,
			onInsertValue: vi.fn(),
			onClose: vi.fn(),
			...props
		}
	});
	flushSync();
}

function typeQuery(text: string) {
	const input = document.querySelector<HTMLInputElement>('.picker-modal-search input')!;
	input.value = text;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

function marks(root: ParentNode = document) {
	return Array.from(root.querySelectorAll('mark')).map((m) => m.textContent);
}

afterEach(() => {
	if (modal) unmount(modal);
	modal = undefined;
	dropdown?.$destroy();
	dropdown = undefined;
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('phrasebook picker search highlighting', () => {
	it('marks the query in modal list rows on the value and Title lines', () => {
		mountModal({ values: values.slice(0, 2) });
		typeQuery('HOUR');

		const rows = Array.from(document.querySelectorAll('.picker-vrow'));
		expect(rows.length).toBe(2);
		expect(marks(rows[0])).toEqual(['hour']);
		expect(rows[0].querySelector('.picker-value-secondary mark')!.textContent).toBe('hour');

		typeQuery('light');
		expect(marks(document.querySelector('.picker-values')!)).toEqual(['light', 'light']);
	});

	it('marks the query in preview grid cards', () => {
		mountModal();
		typeQuery('sha');
		const cards = document.querySelectorAll('.picker-pgitem');
		expect(cards.length).toBe(1);
		expect(marks(cards[0])).toEqual(['sha']);
	});

	it('regex toggle filters and marks with a regular expression', () => {
		mountModal({ values: values.slice(0, 2) });
		typeQuery('^(warm|cool)');
		expect(document.querySelectorAll('.picker-vrow').length).toBe(0);

		const toggle = document.querySelector<HTMLButtonElement>('button[aria-label="Regular expression"]')!;
		expect(toggle.getAttribute('aria-pressed')).toBe('false');
		toggle.click();
		flushSync();

		expect(toggle.getAttribute('aria-pressed')).toBe('true');
		const rows = Array.from(document.querySelectorAll('.picker-vrow'));
		expect(rows.length).toBe(2);
		expect(marks(document.querySelector('.picker-values')!)).toEqual(['warm', 'cool']);
	});

	it('an invalid regex shows an inline error and keeps the list without marks', () => {
		mountModal({ values: values.slice(0, 2) });
		document.querySelector<HTMLButtonElement>('button[aria-label="Regular expression"]')!.click();
		flushSync();
		typeQuery('(warm');

		const error = document.querySelector('.picker-search-error');
		expect(error?.textContent).toMatch(/Invalid regular expression/);
		expect(document.querySelector('.picker-modal-search input')!.getAttribute('aria-invalid')).toBe('true');
		expect(document.querySelectorAll('.picker-vrow').length).toBe(2);
		expect(marks()).toEqual([]);
	});

	it('marks the typed query in # dropdown rows', () => {
		const parentRef = document.createElement('div');
		document.body.appendChild(parentRef);
		dropdown = createClassComponent({
			component: AutocompleteDropdown as never,
			target: freshTarget(),
			props: {
				categories: [],
				suggestions: values.slice(0, 2),
				currentPath: 'lighting.hou',
				triggerChar: '#',
				parentRef,
				onSelectCategory: vi.fn(),
				onSelectValue: vi.fn()
			}
		});
		flushSync();

		const options = Array.from(document.querySelectorAll('[role="option"]'));
		expect(options.length).toBe(2);
		expect(marks(options[0])).toEqual(['hou']);
		expect(marks(options[1])).toEqual(['hou']);
	});

	it('marks the typed query in segment-composer dropdown rows', () => {
		const parentRef = document.createElement('div');
		document.body.appendChild(parentRef);
		dropdown = createClassComponent({
			component: AutocompleteDropdown as never,
			target: freshTarget(),
			props: {
				categories: [],
				suggestions: values.slice(0, 2),
				currentPath: 'warm',
				triggerChar: '#',
				variant: 'segment-composer',
				parentRef,
				onSelectCategory: vi.fn(),
				onSelectValue: vi.fn()
			}
		});
		flushSync();

		const rows = Array.from(document.querySelectorAll('.picker-row.phrase-value'));
		expect(rows.length).toBe(2);
		expect(marks(rows[0].querySelector('.row-copy-value')!)).toEqual(['warm']);
		expect(marks(rows[1])).toEqual([]);
	});
});
