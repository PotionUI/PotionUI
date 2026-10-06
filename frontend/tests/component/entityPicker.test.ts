import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { reactiveProps } from './stubs/reactiveProps.svelte';

const { default: EntityPicker } = await import('../../src/lib/components/picker/EntityPicker.svelte');
const { default: AssignedList } = await import('../../src/lib/components/picker/AssignedList.svelte');
const { presetsKind, groupsKind } = await import('../../src/lib/components/picker/kinds');

import type { PresetInfo } from '../../src/lib/types/api';
import type { UserGroup } from '../../src/lib/services/admin-api';

function preset(id: string, name: string, extra: Partial<PresetInfo> = {}): PresetInfo {
	return {
		id,
		name,
		version: '1.0.0',
		tags: [],
		engine: 'native',
		category: 'image',
		modes: ['txt2img'],
		origin: { kind: 'marketplace', plugin_id: null, path: name },
		...extra
	};
}

const PRESETS: PresetInfo[] = [
	preset('krea-native', 'Krea-2', {
		version: '1.1.0',
		origin: { kind: 'marketplace', plugin_id: null, path: 'Krea2' }
	}),
	preset('krea-comfy', 'Krea 2', {
		engine: 'comfyui',
		version: '1.0.1',
		origin: { kind: 'plugin', plugin_id: 'comfyui-backend', path: 'Krea-2' }
	}),
	preset('sdxl', 'SDXL'),
	preset('wan', 'Wan 2.2', { category: 'video' }),
	preset('flux', 'Flux2')
];

let mounted: ReturnType<typeof mount>[] = [];

function mountPicker(overrides: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const onApply = vi.fn().mockResolvedValue(undefined);
	const onClose = vi.fn();
	const live = reactiveProps<Record<string, unknown>>({
		kind: presetsKind,
		items: PRESETS,
		assignedIds: new Set<string>(['sdxl']),
		isOpen: true,
		title: 'Add presets to mira.k',
		onApply,
		onClose,
		...overrides
	});
	mounted.push(mount(EntityPicker as any, { target, props: live.props as any }));
	flushSync();
	return { live, onApply, onClose };
}

function rowEls(): HTMLElement[] {
	return Array.from(document.querySelectorAll<HTMLElement>('[role="row"][data-row-id]'));
}

function rowById(id: string): HTMLElement {
	return document.querySelector<HTMLElement>(`[role="row"][data-row-id="${id}"]`)!;
}

function viewButton(label: string): HTMLButtonElement {
	return Array.from(document.querySelectorAll<HTMLButtonElement>('nav button')).find((b) => b.textContent?.includes(label))!;
}

function applyButton(): HTMLButtonElement {
	return document.querySelector<HTMLButtonElement>('[role="dialog"] button.bg-accent')!;
}

async function settle() {
	for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

afterEach(() => {
	for (const instance of mounted) unmount(instance);
	mounted = [];
	document.body.innerHTML = '';
});

describe('EntityPicker', () => {
	it('opens on the Not assigned view', () => {
		mountPicker();
		expect(viewButton('Not assigned').getAttribute('aria-current')).toBe('page');
		const ids = rowEls().map((row) => row.dataset.rowId);
		expect(ids).not.toContain('sdxl');
		expect(ids).toHaveLength(4);
	});

	it('shows assigned rows locked under All and unlocked under Assigned', () => {
		mountPicker();
		viewButton('All').click();
		flushSync();
		expect(rowEls()).toHaveLength(5);
		expect(rowById('sdxl').getAttribute('aria-disabled')).toBe('true');
		viewButton('Assigned').click();
		flushSync();
		expect(rowEls().map((row) => row.dataset.rowId)).toEqual(['sdxl']);
		expect(rowById('sdxl').getAttribute('aria-disabled')).toBeNull();
	});

	it('tells the two Krea-2 presets apart by their tags', () => {
		mountPicker();
		const native = rowById('krea-native');
		const comfy = rowById('krea-comfy');
		expect(native.querySelector('[data-same-name]')?.textContent?.trim()).toBe('2 same name');
		expect(comfy.querySelector('[data-same-name]')?.textContent?.trim()).toBe('2 same name');
		const tags = (row: HTMLElement) =>
			Array.from(row.querySelectorAll('[data-fact]')).map((el) => `${(el as HTMLElement).dataset.fact}:${el.textContent?.trim()}`);
		expect(tags(native)).toEqual(['engine:native', 'source:marketplace', 'folder:Krea2', 'version:v1.1.0']);
		expect(tags(comfy)).toEqual(['engine:comfyui', 'source:plugin: comfyui-backend', 'folder:Krea-2', 'version:v1.0.1']);
		expect(rowById('wan').querySelector('[data-same-name]')).toBeNull();
	});

	it('toggles a row from a click anywhere on it and updates the footer diff', () => {
		mountPicker();
		rowById('wan').click();
		rowById('flux').click();
		flushSync();
		expect(rowById('wan').getAttribute('aria-selected')).toBe('true');
		expect(document.querySelector('[data-picker-summary]')?.textContent).toContain('+2 add');
		expect(applyButton().textContent).toContain('Add 2 presets');
	});

	it('selects a range with shift-click over the visible order', () => {
		mountPicker();
		const order = rowEls().map((row) => row.dataset.rowId!);
		rowById(order[0]).click();
		rowById(order[2]).dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true }));
		flushSync();
		const picked = rowEls().filter((row) => row.getAttribute('aria-selected') === 'true').map((row) => row.dataset.rowId);
		expect(picked).toEqual(order.slice(0, 3));
	});

	it('selects every filtered row, not only the page, and survives a search change', () => {
		mountPicker();
		const search = document.querySelector<HTMLInputElement>('input[type="search"]')!;
		search.value = 'krea';
		search.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(rowEls()).toHaveLength(2);
		const link = Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.includes('Select all 2 filtered'))!;
		link.click();
		flushSync();
		search.value = '';
		search.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(rowById('krea-native').getAttribute('aria-selected')).toBe('true');
		expect(rowById('krea-comfy').getAttribute('aria-selected')).toBe('true');
		expect(rowById('wan').getAttribute('aria-selected')).toBe('false');
	});

	it('applies the adds and removes in one call', async () => {
		const { onApply, onClose } = mountPicker({ assignedIds: new Set(['sdxl', 'flux']) });
		viewButton('All').click();
		flushSync();
		viewButton('Assigned').click();
		flushSync();
		rowById('sdxl').click();
		flushSync();
		viewButton('Not assigned').click();
		flushSync();
		rowById('wan').click();
		rowById('krea-native').click();
		flushSync();
		expect(document.querySelector('[data-picker-summary]')?.textContent).toContain('+2 add / -1 remove');
		applyButton().click();
		await settle();
		expect(onApply).toHaveBeenCalledTimes(1);
		const diff = onApply.mock.calls[0][0];
		expect(diff.remove).toEqual(['sdxl']);
		expect([...diff.add].sort()).toEqual(['krea-native', 'wan']);
		expect(onClose).toHaveBeenCalled();
	});

	it('keeps the modal open and flags the failing row on a partial failure', async () => {
		const onApply = vi.fn().mockResolvedValue({ ok: ['wan'], failed: [{ id: 'flux', message: 'Not installed' }] });
		const { onClose } = mountPicker({ onApply });
		rowById('wan').click();
		rowById('flux').click();
		flushSync();
		applyButton().click();
		await settle();
		expect(onClose).not.toHaveBeenCalled();
		expect(rowById('flux').textContent).toContain('Not installed');
		expect(document.body.textContent).toContain('1 of 2 changes failed');
	});

	it('drives the keyboard model: arrows, space, ctrl+a and ctrl+enter', async () => {
		const { onApply } = mountPicker({ assignedIds: new Set<string>() });
		const first = rowEls()[0];
		first.focus();
		first.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
		flushSync();
		expect(first.getAttribute('aria-selected')).toBe('true');
		first.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', ctrlKey: true, bubbles: true }));
		flushSync();
		expect(rowEls().every((row) => row.getAttribute('aria-selected') === 'true')).toBe(true);
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true }));
		await settle();
		expect(onApply).toHaveBeenCalledTimes(1);
		expect(onApply.mock.calls[0][0].add).toHaveLength(5);
	});

	it('escape clears the search first and then closes', () => {
		const { onClose } = mountPicker();
		const search = document.querySelector<HTMLInputElement>('input[type="search"]')!;
		search.value = 'wan';
		search.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();
		expect(onClose).not.toHaveBeenCalled();
		expect(document.querySelector<HTMLInputElement>('input[type="search"]')!.value).toBe('');
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('single mode picks one row and reports it as the addition', async () => {
		const { onApply } = mountPicker({ mode: 'single', assignedIds: new Set(['sdxl']) });
		expect(document.querySelector('nav button')).toBeNull();
		expect(rowEls()).toHaveLength(5);
		rowById('wan').click();
		flushSync();
		expect(rowById('sdxl').getAttribute('aria-selected')).toBe('false');
		applyButton().click();
		await settle();
		expect(onApply.mock.calls[0][0]).toEqual({ add: ['wan'], remove: ['sdxl'] });
	});

	it('shows the empty, error and loading states', () => {
		mountPicker({ items: [], assignedIds: new Set<string>() });
		expect(document.body.textContent).toContain('No presets installed');
		for (const instance of mounted) unmount(instance);
		mounted = [];
		document.body.innerHTML = '';
		const onRetry = vi.fn();
		mountPicker({ error: 'Network down', onRetry });
		expect(document.body.textContent).toContain('Network down');
		Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.includes('Retry'))!.click();
		expect(onRetry).toHaveBeenCalled();
	});

	it('offers to clear filters when a search matches nothing', () => {
		mountPicker();
		const search = document.querySelector<HTMLInputElement>('input[type="search"]')!;
		search.value = 'zzzz';
		search.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(document.body.textContent).toContain('No presets match');
		Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.includes('Clear filters'))!.click();
		flushSync();
		expect(rowEls().length).toBeGreaterThan(0);
	});
});

describe('AssignedList', () => {
	function mountList(overrides: Record<string, unknown> = {}) {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const onApply = vi.fn().mockResolvedValue(undefined);
		mounted.push(
			mount(AssignedList as any, {
				target,
				props: {
					kind: presetsKind,
					label: 'Presets',
					assignedRows: [PRESETS[0], PRESETS[1], PRESETS[2]],
					pickerItems: PRESETS,
					assignedAt: { 'krea-native': new Date().toISOString() },
					onApply,
					...overrides
				}
			})
		);
		flushSync();
		return { onApply };
	}

	it('renders the same rich rows with remove actions', () => {
		mountList();
		const rows = rowEls();
		expect(rows.map((row) => row.dataset.rowId)).toEqual(['krea-comfy', 'krea-native', 'sdxl'].sort((a, b) => {
			const name = (id: string) => PRESETS.find((p) => p.id === id)!.name;
			return name(a).localeCompare(name(b), undefined, { numeric: true });
		}));
		expect(rowById('krea-native').querySelector('[data-same-name]')).not.toBeNull();
		expect(rowById('krea-comfy').textContent).toContain('plugin: comfyui-backend');
		expect(document.querySelector('[data-assigned-count]')?.textContent).toContain('3 assigned');
		expect(document.querySelector('button[aria-label="Remove SDXL"]')).not.toBeNull();
		expect(rowById('krea-native').textContent).toContain('now');
	});

	it('removes a row through onApply', async () => {
		const { onApply } = mountList();
		(document.querySelector('button[aria-label="Remove SDXL"]') as HTMLButtonElement).click();
		await settle();
		expect(onApply).toHaveBeenCalledWith({ add: [], remove: ['sdxl'] });
		expect(rowById('sdxl').getAttribute('aria-selected')).toBe('false');
	});

	it('opens the picker from the Add button on the Not assigned view', () => {
		mountList();
		Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.includes('Add presets'))!.click();
		flushSync();
		expect(document.querySelector('[role="dialog"]')).not.toBeNull();
		expect(viewButton('Not assigned').getAttribute('aria-current')).toBe('page');
	});

	it('shows an empty state with an add action when nothing is assigned', () => {
		mountList({ assignedRows: [] });
		expect(document.body.textContent).toContain('No presets assigned');
	});

	it('hides add and remove when read only', () => {
		mountList({ canEdit: false });
		expect(document.querySelector('button[aria-label="Remove SDXL"]')).toBeNull();
		expect(Array.from(document.querySelectorAll('button')).some((b) => b.textContent?.includes('Add presets'))).toBe(false);
	});

	it('works for the groups kind', () => {
		const groups: UserGroup[] = [{ id: 'g1', name: 'Editors', description: 'Can edit', member_count: 3 }];
		mountList({ kind: groupsKind, label: 'Groups', assignedRows: groups, pickerItems: groups, assignedAt: undefined });
		expect(rowById('g1').textContent).toContain('Can edit');
	});
});
