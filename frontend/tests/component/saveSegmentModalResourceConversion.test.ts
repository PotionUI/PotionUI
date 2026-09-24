// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import type { Segment } from '$lib/types/segments';
import type { PromptResourceSpec } from '$lib/utils/promptResources';

const listSegmentCategories = vi.fn().mockResolvedValue({
	success: true,
	data: { categories: [{ id: 'cat-1', name: 'General', description: '', color: '', user_id: '' }] }
});
const createSavedSegment = vi.fn().mockResolvedValue({ success: true, data: {} });

vi.mock('$lib/services/api', () => ({
	api: { listSegmentCategories: (...a: unknown[]) => listSegmentCategories(...a), createSavedSegment: (...a: unknown[]) => createSavedSegment(...a) }
}));

const { default: SaveSegmentModal } = await import('$lib/components/modals/SaveSegmentModal.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

const specs: PromptResourceSpec[] = [{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' }];

function baseSegment(overrides: Partial<Segment> = {}): Segment {
	return {
		id: 'seg-1',
		content: 'a cat, @[references:a.png]',
		chips: {},
		resources: { 'res-1': { field: 'references', item_key: 'a.png' } },
		enabled: true,
		...overrides
	};
}

function mount(props: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({ component: SaveSegmentModal as never, target, props: { isOpen: true, ...props } });
	flushSync();
}

afterEach(async () => {
	listSegmentCategories.mockClear();
	createSavedSegment.mockClear();
	target?.remove();
	document.body.innerHTML = '';
});

describe('SaveSegmentModal resource-reference conversion', () => {
	it('shows no conversion warning for a segment without resource markers', async () => {
		mount({ segment: baseSegment({ content: 'a plain cat', resources: {} }), promptResources: specs, resourceFieldValues: {} });
		await new Promise((r) => setTimeout(r, 0));
		flushSync();
		expect(document.body.textContent).not.toContain('references media uploaded on this tab');
	});

	it('shows the conversion warning for a segment with resource markers, defaulting to convert', async () => {
		mount({
			segment: baseSegment(),
			promptResources: specs,
			resourceFieldValues: { references: [{ relative_path: 'a.png' }] }
		});
		await new Promise((r) => setTimeout(r, 0));
		flushSync();
		expect(document.body.textContent).toContain('references media uploaded on this tab');
		const radios = document.body.querySelectorAll<HTMLInputElement>('input[type="radio"]');
		expect(radios[0].checked).toBe(true);
		expect(radios[1].checked).toBe(false);
	});

	it('saves resolved plain text and clears resources when converting', async () => {
		mount({
			segment: baseSegment(),
			promptResources: specs,
			resourceFieldValues: { references: [{ relative_path: 'a.png' }] }
		});
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		const nameInput = document.body.querySelector<HTMLInputElement>('#saved-segment-name')!;
		nameInput.value = 'My segment';
		nameInput.dispatchEvent(new Event('input', { bubbles: true }));
		const categorySelect = document.body.querySelector<HTMLSelectElement>('#saved-segment-category')!;
		categorySelect.value = 'cat-1';
		categorySelect.dispatchEvent(new Event('change', { bubbles: true }));
		flushSync();

		const saveButton = Array.from(document.body.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Save Segment')!;
		saveButton.click();
		await new Promise((r) => setTimeout(r, 0));

		expect(createSavedSegment).toHaveBeenCalledTimes(1);
		const payload = createSavedSegment.mock.calls[0][0];
		expect(payload.content).toBe('a cat, <Picture 1>');
		expect(payload.resources ?? {}).toEqual({});
	});

	it('saves the raw marker text and keeps the resources map when the user opts to keep references', async () => {
		mount({
			segment: baseSegment(),
			promptResources: specs,
			resourceFieldValues: { references: [{ relative_path: 'a.png' }] }
		});
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		const radios = document.body.querySelectorAll<HTMLInputElement>('input[type="radio"]');
		radios[1].dispatchEvent(new Event('change', { bubbles: true }));
		flushSync();

		const nameInput = document.body.querySelector<HTMLInputElement>('#saved-segment-name')!;
		nameInput.value = 'My segment';
		nameInput.dispatchEvent(new Event('input', { bubbles: true }));
		const categorySelect = document.body.querySelector<HTMLSelectElement>('#saved-segment-category')!;
		categorySelect.value = 'cat-1';
		categorySelect.dispatchEvent(new Event('change', { bubbles: true }));
		flushSync();

		const saveButton = Array.from(document.body.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Save Segment')!;
		saveButton.click();
		await new Promise((r) => setTimeout(r, 0));

		expect(createSavedSegment).toHaveBeenCalledTimes(1);
		const payload = createSavedSegment.mock.calls[0][0];
		expect(payload.content).toBe('a cat, @[references:a.png]');
		expect(payload.resources).toEqual({ 'res-1': { field: 'references', item_key: 'a.png' } });
	});
});
