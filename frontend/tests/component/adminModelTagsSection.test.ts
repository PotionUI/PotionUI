// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { readable } from 'svelte/store';

const catalog = [
	{ id: 't1', name: 'checkpoint', type: 'MODEL' },
	{ id: 't2', name: 'lora', type: 'MODEL' },
	{ id: 't3', name: 'style', type: 'MODEL' }
];

vi.mock('$lib/stores/tagCatalog', () => ({
	tagCatalog: {
		tags: () => readable(catalog),
		get: vi.fn().mockResolvedValue(catalog),
		create: vi.fn(),
		reset: vi.fn()
	}
}));

const { default: ModelTagsSection } = await import('../../src/routes/admin/components/models/ModelTagsSection.svelte');
const { default: ModelRowTags } = await import('../../src/routes/admin/components/models/ModelRowTags.svelte');

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

let instance: object | undefined;
let target: HTMLElement | undefined;

function render(component: unknown, props: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(component as never, { target, props });
	return target;
}

afterEach(() => {
	if (instance) unmount(instance as never);
	target?.remove();
	instance = undefined;
});

describe('ModelTagsSection', () => {
	it('shows current tags and an Add tag control without any hover', async () => {
		const host = render(ModelTagsSection, { tags: [catalog[0]], onChange: vi.fn() });
		await settle();
		expect(host.textContent).toContain('Tags');
		expect(host.querySelector('[data-testid="model-tags"]')?.textContent).toContain('checkpoint');
		expect(host.textContent).toContain('Add tag');
	});

	it('removes a tag with its remove button', async () => {
		const onChange = vi.fn();
		const host = render(ModelTagsSection, { tags: [catalog[0], catalog[1]], onChange });
		await settle();
		(host.querySelector('button[aria-label="Remove checkpoint"]') as HTMLButtonElement).click();
		expect(onChange).toHaveBeenCalledWith(['t2']);
	});

	it('adds a tag picked from the selector', async () => {
		const onChange = vi.fn();
		const host = render(ModelTagsSection, { tags: [catalog[0]], onChange });
		await settle();
		const trigger = [...host.querySelectorAll('button')].find((b) => b.textContent?.includes('Add tag')) as HTMLButtonElement;
		trigger.click();
		await settle();
		const option = [...document.querySelectorAll('[role="option"]')].find((o) => o.textContent?.includes('lora')) as HTMLElement;
		option.click();
		await settle();
		expect(onChange).toHaveBeenCalledWith(['t1', 't2']);
	});
});

describe('ModelRowTags', () => {
	it('shows at most two chips plus a count', async () => {
		const host = render(ModelRowTags, { tags: catalog });
		await settle();
		const text = host.querySelector('[data-testid="row-tags"]')?.textContent ?? '';
		expect(text).toContain('checkpoint');
		expect(text).toContain('lora');
		expect(text).not.toContain('style');
		expect(text).toContain('+1');
	});

	it('shows a dash when untagged', async () => {
		const host = render(ModelRowTags, { tags: [] });
		await settle();
		expect(host.textContent).toContain('—');
	});
});
