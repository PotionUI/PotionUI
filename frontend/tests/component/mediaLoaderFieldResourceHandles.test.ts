// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { writable } from 'svelte/store';
import type { PromptResourceUsage } from '$lib/utils/promptResourceUsage';

vi.mock('$lib/services/api/index', () => ({
	api: {
		listGenerationMedia: vi.fn().mockResolvedValue({ success: false }),
		getUploadInfo: vi.fn().mockResolvedValue({ success: false }),
		getHistoryTools: vi.fn().mockResolvedValue({ success: true, data: [] }),
		listUploads: vi
			.fn()
			.mockResolvedValue({ success: true, data: { uploads: [], total: 0, limit: 100, offset: 0 } }),
		editMediaItem: vi.fn(),
		extractMediaFrame: vi.fn(),
		listLibraryItems: vi.fn().mockResolvedValue({ success: true, data: { items: [], total: 0 } }),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } })
	}
}));

vi.mock('$lib/utils/storage', () => ({
	storage: { get: vi.fn().mockReturnValue(null), set: vi.fn(), remove: vi.fn() }
}));

const { default: MediaLoaderField } = await import('$lib/components/form-fields/MediaLoaderField.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');
const { PROMPT_RESOURCE_USAGE_CONTEXT_KEY } = await import('$lib/utils/promptResourceUsage');

function imageItem(id: string) {
	return {
		path: `uploads/${id}.png`,
		relative_path: `uploads/${id}.png`,
		url: `/api/media/uploads/${id}.png`,
		name: `${id}.png`,
		type: 'image'
	};
}

const spec = { field: 'references', kind: 'image' as const, label: 'Pictures', token: '<Picture @>' };

function mount(props: Record<string, unknown>, usage?: ReturnType<typeof writable<PromptResourceUsage>>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const context = new Map<unknown, unknown>();
	if (usage) context.set(PROMPT_RESOURCE_USAGE_CONTEXT_KEY, usage);
	const component = createClassComponent({ component: MediaLoaderField as never, target, props, context });
	return { target, component };
}

function handles(target: HTMLElement): string[] {
	return Array.from(target.querySelectorAll<HTMLElement>('[data-resource-handle]')).map((el) => {
		const long = el.querySelector('.hidden')?.textContent?.trim() ?? '';
		const uses = el.querySelector('[data-resource-uses]')?.textContent?.trim();
		return uses ? `${long} ${uses}` : long;
	});
}

function tiles(target: HTMLElement): HTMLElement[] {
	return Array.from(target.querySelectorAll<HTMLElement>('[data-media-tile]'));
}

const baseProps = {
	name: 'references',
	config: { title: 'Reference images', accept: 'image/*', multiple: true },
	value: [imageItem('a'), imageItem('b'), imageItem('c')],
	onChange: vi.fn()
};

describe('MediaLoaderField resource handles', () => {
	it('shows the selected item its handle and a use count only when referenced', async () => {
		const usage = writable<PromptResourceUsage>({
			specs: [spec],
			counts: { references: { 'uploads/b.png': 2, 'uploads/c.png': 1 } }
		});
		const { target } = mount(baseProps, usage);
		await tick();
		expect(handles(target)).toEqual(['Picture 1']);

		tiles(target)[1].click();
		await tick();
		expect(handles(target)).toEqual(['Picture 2 ×2']);

		tiles(target)[2].click();
		await tick();
		expect(handles(target)).toEqual(['Picture 3 ×1']);
	});

	it('updates the counts live as the prompt references change', async () => {
		const usage = writable<PromptResourceUsage>({ specs: [spec], counts: {} });
		const { target } = mount(baseProps, usage);
		await tick();
		expect(target.querySelectorAll('[data-resource-uses]').length).toBe(0);
		usage.set({ specs: [spec], counts: { references: { 'uploads/a.png': 3 } } });
		await tick();
		expect(handles(target)[0]).toBe('Picture 1 ×3');
	});

	it('renumbers the handle after a reorder while the count follows the item', async () => {
		const usage = writable<PromptResourceUsage>({
			specs: [spec],
			counts: { references: { 'uploads/a.png': 1 } }
		});
		const { target, component } = mount(baseProps, usage);
		await tick();
		tiles(target)[1].click();
		await tick();
		expect(handles(target)).toEqual(['Picture 2']);
		component.$set({ value: [imageItem('b'), imageItem('a'), imageItem('c')] });
		await tick();
		expect(handles(target)).toEqual(['Picture 2 ×1']);
	});

	it('keeps the plain position label for a field the preset does not map', async () => {
		const usage = writable<PromptResourceUsage>({ specs: [spec], counts: {} });
		const { target } = mount({ ...baseProps, name: 'style_refs' }, usage);
		await tick();
		expect(handles(target)).toEqual([]);
		expect(target.querySelector('[data-media-handle]')?.textContent).toContain('Picture 1');
		expect(tiles(target)).toHaveLength(3);
	});

	it('shows no prompt handle without a prompt resource context', async () => {
		const { target } = mount(baseProps);
		await tick();
		expect(handles(target)).toEqual([]);
	});

	it('takes the handle of a mixed field from the spec of the selected item\'s own kind', async () => {
		const specs = [
			{ field: 'references', kind: 'image' as const, token: '<Picture @>' },
			{ field: 'references', kind: 'video' as const, token: '<Video @>' }
		];
		const usage = writable<PromptResourceUsage>({
			specs,
			counts: { references: { 'uploads/v.mp4': 2 } }
		});
		const { target } = mount(
			{
				name: 'references',
				config: { title: 'References', accepted_types: ['image', 'video'], multiple: true },
				value: [
					imageItem('a'),
					{ path: 'uploads/v.mp4', relative_path: 'uploads/v.mp4', url: '/api/media/uploads/v.mp4', name: 'v.mp4', type: 'video' },
					imageItem('b')
				],
				onChange: vi.fn()
			},
			usage
		);
		await tick();
		expect(handles(target)).toEqual(['Picture 1']);

		target.querySelector<HTMLElement>('[data-media-group="video"] [data-media-tile]')!.click();
		await tick();
		expect(handles(target)).toEqual(['Video 1 ×2']);
	});
});
