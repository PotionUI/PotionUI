// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { Writable } from 'svelte/store';
import { catalogFixture, ruleFixture } from './organizeFixtures';

type PageStore = Writable<{ url: URL }>;

const api = vi.hoisted(() => ({
	getOrganizeCatalog: vi.fn(),
	listOrganizeRules: vi.fn(),
	getOrganizeSummary: vi.fn(),
	getOrganizeRule: vi.fn(),
	patchOrganizeRule: vi.fn(),
	duplicateOrganizeRule: vi.fn(),
	deleteOrganizeRule: vi.fn(),
	reorderOrganizeRules: vi.fn(),
	previewOrganizeRule: vi.fn(),
	getOrganizeActivity: vi.fn(),
	getOrganizeFactOptions: vi.fn(),
	listCollections: vi.fn(),
	listModelCollections: vi.fn(),
	getModelById: vi.fn()
}));

vi.mock('$lib/services/api', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api')>('$lib/services/api');
	return { ...actual, api: { ...actual.api, ...api } };
});
vi.mock('$app/navigation', async () => {
	const { page } = await import('$app/stores');
	const store = page as unknown as PageStore;
	return {
		goto: async (href: string) => {
			store.update((current) => ({ ...current, url: new URL(href, 'http://localhost') }));
		},
		invalidate: async () => {},
		invalidateAll: async () => {},
		preloadData: async () => {},
		preloadCode: async () => {},
		afterNavigate: () => {},
		beforeNavigate: () => {},
		pushState: () => {},
		replaceState: () => {}
	};
});

const { page } = await import('$app/stores');
const { default: Page } = await import('../../src/routes/auto-organize/+page.svelte');
const { startRuleFrom } = await import('../../src/lib/organize/handoff');

const pageStore = page as unknown as PageStore;

async function wait(ms = 0) {
	await new Promise((resolve) => setTimeout(resolve, ms));
	flushSync();
}

async function settle() {
	for (let i = 0; i < 8; i++) await wait();
}

let instance: ReturnType<typeof mount> | null = null;
let target: HTMLElement;

function summary(over: Record<string, unknown> = {}) {
	return {
		success: true,
		data: {
			paused_by_admin: false,
			rule_cap: 50,
			rule_count: 1,
			hourly_limit: 200,
			subjects: {
				generation: { total: 1, active: 1, needs_attention: 0 },
				upload: { total: 0, active: 0, needs_attention: 0 },
				model: { total: 0, active: 0, needs_attention: 0 }
			},
			...over
		}
	};
}

function open(search: string) {
	pageStore.set({ url: new URL(`http://localhost/auto-organize${search}`) } as never);
}

beforeEach(() => {
	target = document.createElement('div');
	document.body.appendChild(target);
	for (const fn of Object.values(api)) fn.mockReset();
	api.getOrganizeCatalog.mockResolvedValue({ success: true, data: catalogFixture });
	api.listOrganizeRules.mockResolvedValue({ success: true, data: [ruleFixture()] });
	api.getOrganizeSummary.mockResolvedValue(summary());
	api.previewOrganizeRule.mockResolvedValue({
		success: true,
		data: { matched: 3, already_handled: 0, would_change: 3, approximate: false, sample: [], duplicates: [] }
	});
	api.listCollections.mockResolvedValue({ success: true, data: { collections: [], total: 0 } });
	api.listModelCollections.mockResolvedValue({ success: true, data: { collections: [], total: 0 } });
	api.getOrganizeFactOptions.mockResolvedValue({ success: true, data: [] });
	api.getModelById.mockResolvedValue({ success: true, data: { model: { id: 'm1', name: 'Krea-2 Turbo' } } });
	api.getOrganizeActivity.mockResolvedValue({ success: true, data: { runs: [], next_before: null } });
	sessionStorage.clear();
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	target.remove();
});

function mountPage() {
	instance = mount(Page, { target });
}

describe('Auto-organize page', () => {
	it('lists the rules of the subject as sentences with their status and templates', async () => {
		open('?subject=generations');
		mountPage();
		await settle();
		expect(api.listOrganizeRules).toHaveBeenCalledWith('generation');
		const card = target.querySelector('[data-testid="rule-card"]')!;
		expect(card.textContent).toContain('Videos');
		expect(card.textContent).toContain('Media kind is Video');
		expect(card.textContent).toContain('add to Videos');
		expect(target.querySelectorAll('[data-testid="rule-template"]').length).toBeGreaterThan(0);
	});

	it('switches a rule off with the switch', async () => {
		api.patchOrganizeRule.mockResolvedValue({ success: true, data: ruleFixture({ enabled: false, status: 'off' }) });
		open('?subject=generations');
		mountPage();
		await settle();
		(target.querySelector('[data-testid="rule-card"] input[role="switch"]') as HTMLInputElement).click();
		await settle();
		expect(api.patchOrganizeRule).toHaveBeenCalledWith('r1', { enabled: false });
	});

	it('explains a rule that was paused by the safety valve', async () => {
		api.listOrganizeRules.mockResolvedValue({
			success: true,
			data: [ruleFixture({ status: 'paused', paused_reason: 'rate_limited', enabled: false })]
		});
		open('?subject=generations');
		mountPage();
		await settle();
		expect(target.textContent).toContain('Paused');
		expect(target.textContent).toContain('it was paused. Check it and switch it back on');
	});

	it('says so when an administrator paused everything', async () => {
		api.getOrganizeSummary.mockResolvedValue(summary({ paused_by_admin: true }));
		open('?subject=generations');
		mountPage();
		await settle();
		expect(target.textContent).toContain('paused by an administrator');
	});

	it('opens the editor prefilled when a rule is made from a filter', async () => {
		open('?subject=generations');
		mountPage();
		await settle();
		await startRuleFrom({
			subject: 'generation',
			name: 'Krea landscapes',
			conditions: [
				{ fact: 'model', operator: 'is', value: 'm1' },
				{ fact: 'resolution', operator: 'is', value: { width: 1344, height: 768 } }
			]
		});
		await settle();
		expect((target.querySelector('input[aria-label="Rule name"]') as HTMLInputElement).value).toBe('Krea landscapes');
		const facts = [...target.querySelectorAll('[data-testid="condition-row"] [data-fact]')].map((el) => el.getAttribute('data-fact'));
		expect(facts).toEqual(['model', 'resolution']);
		expect(target.textContent).toContain('Krea-2 Turbo');
		expect(target.textContent).toContain('All rules');
	});

	it('starts a new rule from a template', async () => {
		open('?subject=generations');
		mountPage();
		await settle();
		const template = [...target.querySelectorAll('[data-testid="rule-template"]')].find((el) => el.textContent?.includes('Videos in one place')) as HTMLButtonElement;
		template.click();
		await settle();
		expect((target.querySelector('input[aria-label="Rule name"]') as HTMLInputElement).value).toBe('Videos');
		expect(target.querySelector('[data-testid="action-row"][data-action="add_to_collection"]')).not.toBeNull();
	});

	it('follows a rule link from a notification to the right subject', async () => {
		api.getOrganizeRule.mockResolvedValue({ success: true, data: ruleFixture({ id: 'r7', name: 'Model sorter', subject: 'model', conditions: [], actions: [{ action: 'add_to_collection', config: { collection_id: 'c9', collection_name: 'SDXL' } }] }) });
		open('?rule=r7');
		api.listOrganizeRules.mockResolvedValue({ success: true, data: [] });
		mountPage();
		await settle();
		expect(api.getOrganizeRule).toHaveBeenCalledWith('r7');
		expect((target.querySelector('input[aria-label="Rule name"]') as HTMLInputElement).value).toBe('Model sorter');
	});

	it('shows the activity view', async () => {
		open('?subject=generations&view=activity');
		mountPage();
		await settle();
		expect(api.getOrganizeActivity).toHaveBeenCalled();
		expect(target.textContent).toContain('No activity yet');
	});
});
