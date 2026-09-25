// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const getModelSelectionTags = vi.fn();
const getTags = vi.fn();
const bulkUpdateModelTags = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getModelSelectionTags: (...args: unknown[]) => getModelSelectionTags(...args),
		getTags: (...args: unknown[]) => getTags(...args),
		bulkUpdateModelTags: (...args: unknown[]) => bulkUpdateModelTags(...args)
	}
}));

const { default: BulkTagModal } = await import('../../src/routes/admin/components/models/BulkTagModal.svelte');

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

let instance: ReturnType<typeof mount> | null = null;

function mountModal(onClose = vi.fn(), onApplied = vi.fn()) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(BulkTagModal, {
		target,
		props: { isOpen: true, modelIds: ['m1', 'm2', 'm3', 'm4', 'm5'], onClose, onApplied }
	});
	return { onClose, onApplied };
}

function typeAndEnter(value: string) {
	const input = document.getElementById('bulk-tag-input') as HTMLInputElement;
	input.value = value;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
	flushSync();
}

function applyButton(): HTMLButtonElement {
	return [...document.querySelectorAll('button')].find((b) => b.textContent?.trim().startsWith('Apply')) as HTMLButtonElement;
}

beforeEach(() => {
	getModelSelectionTags.mockResolvedValue({
		success: true,
		data: {
			models: 5,
			tags: [
				{ id: 't1', name: 'anime', count: 3 },
				{ id: 't2', name: 'style', count: 5 }
			]
		}
	});
	getTags.mockResolvedValue({ success: true, data: { tags: [{ id: 't1', name: 'anime' }, { id: 't2', name: 'style' }, { id: 't3', name: 'portrait' }] } });
	bulkUpdateModelTags.mockResolvedValue({
		success: true,
		data: {
			models: 5,
			unknown_model_ids: [],
			unknown_tags: [],
			tags: [
				{ id: 't9', name: 'cinematic', added: 5, already_present: 0, removed: 0 },
				{ id: 't1', name: 'anime', added: 0, already_present: 0, removed: 3 }
			]
		}
	});
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('BulkTagModal', () => {
	it('shows the title and the tags on the selection with counts', async () => {
		mountModal();
		await settle();

		expect(document.body.textContent).toContain('Tags — 5 models');
		expect(getModelSelectionTags).toHaveBeenCalledWith(['m1', 'm2', 'm3', 'm4', 'm5']);
		const list = document.querySelector('[data-testid="bulk-tag-selection"]')!;
		const rows = [...list.querySelectorAll('li')].map((li) => li.textContent?.replace(/\s+/g, ' ').trim());
		expect(rows[0]).toContain('style');
		expect(rows[0]).toContain('5/5');
		expect(rows[1]).toContain('anime');
		expect(rows[1]).toContain('3/5');
		expect(applyButton().disabled).toBe(true);
	});

	it('sends added names and removed ids, then reports and closes', async () => {
		const { onClose, onApplied } = mountModal();
		await settle();

		typeAndEnter('cinematic');
		expect(document.querySelector('[data-testid="bulk-tag-add-chips"]')?.textContent).toContain('cinematic');

		const removeAnime = document.querySelector('button[aria-label="Remove anime"]') as HTMLButtonElement;
		removeAnime.click();
		flushSync();
		expect(document.body.textContent).toContain('Removing');

		applyButton().click();
		await settle();

		expect(bulkUpdateModelTags).toHaveBeenCalledWith({
			model_ids: ['m1', 'm2', 'm3', 'm4', 'm5'],
			add: ['cinematic'],
			remove: ['t1']
		});
		expect(onApplied).toHaveBeenCalledTimes(1);
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('keeps the modal open when the request fails', async () => {
		bulkUpdateModelTags.mockResolvedValueOnce({ success: false, message: 'nope' });
		const { onClose, onApplied } = mountModal();
		await settle();

		typeAndEnter('cinematic');
		applyButton().click();
		await settle();

		expect(onApplied).not.toHaveBeenCalled();
		expect(onClose).not.toHaveBeenCalled();
	});
});
