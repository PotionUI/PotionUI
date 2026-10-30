import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Writable } from 'svelte/store';
import type { SegmentTemplate } from '$lib/types/segments';
import { settle } from './helpers/settle';

type PageStore = Writable<{ url: URL }>;

vi.mock('$lib/services/api/index', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/services/api/index')>();
	return {
		...actual,
		api: {
			...actual.api,
			listSegmentTemplates: vi.fn()
		}
	};
});

const { api } = await import('$lib/services/api/index');
vi.spyOn(api, 'getClient').mockReturnValue({
	get: vi.fn().mockResolvedValue({ data: { data: {} } }),
	put: vi.fn().mockResolvedValue({ data: { success: true } })
} as never);
const page = (await import('$app/stores')).page as unknown as PageStore;
const { activeTab, tabsStore } = await import('$lib/stores/tabs');
const { get } = await import('svelte/store');
const { default: TemplatesWorkspace } = await import('../../src/routes/prompts/sections/TemplatesWorkspace.svelte');
const { createClassComponent } = await import('svelte/legacy');

const template: SegmentTemplate = {
	id: 'tpl-1',
	name: 'Portrait lighting',
	description: null,
	tags: [],
	segments: [{ type: 'content', content: 'soft light', chips: {}, enabled: true, name: 'Light' }]
};

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

beforeEach(() => {
	page.update((current) => ({ ...current, url: new URL('http://localhost/prompts?section=templates&id=tpl-1') }));
	vi.mocked(api.listSegmentTemplates).mockResolvedValue({ success: true, data: { templates: [template] } } as never);
	tabsStore.updateTab(get(activeTab).id, {
		variables: { mood: { type: 'choice', mode: 'shuffle', pinnedIndex: null, options: ['calm', 'tense'] } }
	});
});

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target?.remove();
	tabsStore.updateTab(get(activeTab).id, { variables: {} });
	vi.clearAllMocks();
});

describe('templates workspace edit view', () => {
	it('passes the active tab variables to the template editor', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		component = createClassComponent({ component: TemplatesWorkspace as never, target, props: {} });
		await settle();

		const rows = Array.from(target.querySelectorAll('[data-testid="variables-list"] li')).map((row) =>
			row.textContent?.replace(/\s+/g, ' ').trim()
		);
		expect(rows).toEqual(['$mood shuffle · 2 options']);
		expect(target.querySelector('button[aria-label="Variables (1)"]')).not.toBeNull();
	});
});
