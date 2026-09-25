// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		searchPhrasebook: vi.fn().mockResolvedValue({ success: true, data: { child_categories: [], values: [] } }),
		getFileURL: (fileId: string, size: string) => `/media/${size}/${fileId}`
	}
}));

const { api } = await import('$lib/services/api/index');
const { default: InlineChipEditor } = await import('$lib/components/InlineChipEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { flushSync } = await import('svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

interface EditorInstance {
	insertPhrasebookTrigger: () => void;
}

function mountEditor(props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: InlineChipEditor as never,
		target,
		props: { value: '', chips: {}, variant: 'segment-composer', borderless: true, ...props }
	});
	return component as unknown as EditorInstance;
}

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('AutocompleteDropdown restyle (segment-composer)', () => {
	it('renders the trigger glyph and the quoted query in the header', async () => {
		vi.mocked(api.searchPhrasebook).mockResolvedValue({
			success: true,
			data: { child_categories: [], values: [] }
		} as never);
		const editor = mountEditor();
		editor.insertPhrasebookTrigger();
		flushSync();

		const head = document.querySelector('.picker-head');
		expect(head).not.toBeNull();
		expect(head!.querySelector('.picker-symbol')!.textContent).toBe('#');
		expect(head!.querySelector('.picker-query')!.textContent).toContain('Phrasebook');

		document.execCommand?.('insertText');
		const textNode = document.querySelector('.inline-chip-editor')!.firstChild as Text;
		textNode.textContent = '#li';
		const sel = window.getSelection()!;
		const range = document.createRange();
		range.setStart(textNode, 3);
		range.collapse(true);
		sel.removeAllRanges();
		sel.addRange(range);
		document.querySelector('.inline-chip-editor')!.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await new Promise((r) => setTimeout(r, 210));
		flushSync();

		expect(document.querySelector('.picker-query')!.textContent).toContain('li');
	});

	it('renders a thumbnail for a phrasebook value with a preview image, and a glyph tile otherwise', async () => {
		vi.mocked(api.searchPhrasebook).mockResolvedValue({
			success: true,
			data: {
				child_categories: [],
				values: [
					{
						id: 'v1',
						category_id: 'c1',
						label: 'golden hour',
						value: 'golden hour lighting',
						sort_order: 0,
						created_at: '',
						updated_at: '',
						preview_file_id: 'file-1'
					},
					{
						id: 'v2',
						category_id: 'c1',
						label: 'low key fog',
						value: 'low key harbor fog',
						sort_order: 1,
						created_at: '',
						updated_at: ''
					}
				]
			}
		} as never);

		const editor = mountEditor();
		editor.insertPhrasebookTrigger();
		flushSync();
		await new Promise((r) => setTimeout(r, 210));
		flushSync();

		const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-row.phrase-value'));
		expect(rows).toHaveLength(2);
		expect(rows[0].querySelector('img')).not.toBeNull();
		expect(rows[0].querySelector('img')!.getAttribute('src')).toBe('/media/small/file-1');
		expect(rows[0].querySelector('img')!.getAttribute('loading')).toBe('lazy');
		expect(rows[1].querySelector('img')).toBeNull();
		expect(rows[1].querySelector('.row-thumb-glyph')!.textContent).toBe('#');
	});

	it('ArrowDown navigates and Enter inserts the selected value', async () => {
		vi.mocked(api.searchPhrasebook).mockResolvedValue({
			success: true,
			data: {
				child_categories: [],
				values: [
					{ id: 'v1', category_id: 'c1', category_path: 'lighting', label: 'golden hour', value: 'golden hour lighting', sort_order: 0, created_at: '', updated_at: '' },
					{ id: 'v2', category_id: 'c1', category_path: 'lighting', label: 'low key fog', value: 'low key harbor fog', sort_order: 1, created_at: '', updated_at: '' }
				]
			}
		} as never);

		const editor = mountEditor();
		editor.insertPhrasebookTrigger();
		flushSync();
		await new Promise((r) => setTimeout(r, 210));
		flushSync();

		const editorEl = document.querySelector('.inline-chip-editor')!;
		editorEl.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
		flushSync();

		const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-row.phrase-value'));
		expect(rows[1].className).toContain('selected');

		editorEl.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();

		expect(document.querySelector('.phrasebook-picker')).toBeNull();
		expect(target.textContent).toContain('low key fog');
	});

	it('Tab opens the browse modal instead of moving focus out of the editor', async () => {
		vi.mocked(api.searchPhrasebook).mockResolvedValue({
			success: true,
			data: { child_categories: [], values: [] }
		} as never);

		const editor = mountEditor();
		editor.insertPhrasebookTrigger();
		flushSync();
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		const editorEl = document.querySelector('.inline-chip-editor')!;
		const tabEvent = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
		editorEl.dispatchEvent(tabEvent);
		flushSync();

		expect(tabEvent.defaultPrevented).toBe(true);
		expect(document.querySelector('[role="dialog"]')).not.toBeNull();
		expect(document.querySelector('.picker-chip')!.textContent).toBe('#');
		expect(document.querySelector('.phrasebook-picker')).toBeNull();
	});
});
