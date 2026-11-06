import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from './helpers/mount';
import { advanceTimers } from './helpers/settle';
import { instanceCatalogFixture } from './organizeFixtures';

const client = vi.hoisted(() => ({
	get: vi.fn(),
	post: vi.fn(),
	put: vi.fn(),
	patch: vi.fn(),
	delete: vi.fn()
}));
const getUserGroups = vi.hoisted(() => vi.fn());
const api = vi.hoisted(() => ({
	listModelCollections: vi.fn(),
	getOrganizeFactOptions: vi.fn(),
	previewOrganizeRule: vi.fn()
}));

vi.mock('$lib/services/api', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api')>('$lib/services/api');
	return { ...actual, api: { ...actual.api, ...api, getClient: () => client } };
});
vi.mock('$lib/services/admin-api', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/admin-api')>('$lib/services/admin-api');
	return { ...actual, getUserGroups };
});

const { default: Host } = await import('./stubs/OrganizeScopeHost.svelte');
const { default: RuleEditor } = await import('../../src/lib/components/organize/RuleEditor.svelte');
const { default: ActionBuilder } = await import('../../src/lib/components/organize/ActionBuilder.svelte');
const { draftFromParts } = await import('../../src/lib/organize/draft');
const { instanceRuleFixture } = await import('./organizeFixtures');

const BASE = '/api/admin/organize/instance';

function reply(data: unknown) {
	return Promise.resolve({ data: { success: true, data } });
}

function previewData(over: Record<string, unknown> = {}) {
	return {
		matched: 14,
		already_handled: 0,
		would_change: 14,
		approximate: false,
		sample: ['krea2_dev_bf16', 'krea2_dev_fp8', 'krea2_turbo_fp8', 'krea2_edit_fp8', 'krea2_base_bf16'].map((name, i) => ({
			item_type: 'model',
			item_id: `m${i}`,
			name,
			model_type: 'checkpoint'
		})),
		duplicates: [],
		...over
	};
}

function matchesPage(page: number) {
	return {
		total: 60,
		page,
		page_size: 25,
		approximate: false,
		counts: { would_change: 40, already_done: 20 },
		items: Array.from({ length: 25 }, (_, i) => ({
			item_type: 'model',
			item_id: `m${page}-${i}`,
			name: `krea2_p${page}_${i}`,
			model_type: 'checkpoint',
			status: 'would_change'
		}))
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await advanceTimers(0);
}

function urls(fn: ReturnType<typeof vi.fn>): string[] {
	return fn.mock.calls.map((call) => String(call[0]));
}

function allUrls(): string[] {
	return [client.get, client.post, client.put, client.patch, client.delete].flatMap(urls);
}

function button(root: ParentNode, label: string): HTMLButtonElement {
	const found = [...root.querySelectorAll('button')].find((b) => b.textContent?.trim() === label);
	if (!found) throw new Error(`no button ${label}`);
	return found as HTMLButtonElement;
}

let target: HTMLElement;

function renderEditor(initial: ReturnType<typeof draftFromParts>, scope: 'instance' | 'user' = 'instance', catalog = instanceCatalogFixture) {
	target = mountComponent(Host, {
		component: RuleEditor,
		scope,
		props: { initial, catalog, onBack: vi.fn(), onSaved: vi.fn() }
	}).target;
}

function kreaDraft() {
	return draftFromParts('model', {
		name: 'Krea-2 models to krea-2',
		conditions: [{ fact: 'name', operator: 'contains', value: 'krea2', ignore_separators: true }],
		actions: [{ action: 'assign_model_group', config: { group_id: 'g1' } }]
	});
}

function civitaiDraft() {
	return draftFromParts('model', {
		name: 'Fetch CivitAI data',
		conditions: [
			{ fact: 'source', operator: 'is_not', value: 'civitai' },
			{ fact: 'model_type', operator: 'is_any_of', value: ['checkpoint', 'lora'] }
		],
		actions: [{ action: 'civitai-provider.fetch_info', config: {} }]
	});
}

beforeEach(() => {
	vi.useFakeTimers();
	for (const fn of [client.get, client.post, client.put, client.patch, client.delete, getUserGroups, ...Object.values(api)]) fn.mockReset();
	getUserGroups.mockResolvedValue({
		success: true,
		data: [
			{ id: 'g1', name: 'krea-2', member_count: 6 },
			{ id: 'g2', name: 'flux-team', member_count: 1 }
		]
	});
	api.listModelCollections.mockResolvedValue({ success: true, data: { collections: [], total: 0 } });
	client.post.mockImplementation((url: string) => {
		if (url.endsWith('/preview')) return reply(previewData());
		if (url.endsWith('/preview/matches')) return reply(matchesPage(1));
		return reply(null);
	});
});

afterEach(() => {
	vi.useRealTimers();
});

describe('instance rule editor: Krea-2 example', () => {
	it('shows the group picker with member counts and the ignore dashes and underscores switch', async () => {
		renderEditor(kreaDraft());
		await settle();
		expect(target.querySelector('[data-action="assign_model_group"]')).not.toBeNull();
		expect(getUserGroups).toHaveBeenCalledTimes(1);
		const picker = target.querySelector('[data-action="assign_model_group"] input[aria-haspopup="listbox"]') as HTMLInputElement;
		expect(picker.value).toBe('krea-2 · 6 members');
		expect(target.textContent).toContain('Members of the group see these models in their model lists.');
		const toggle = target.querySelector('[data-testid="ignore-separators"] input') as HTMLInputElement;
		expect(toggle).not.toBeNull();
		expect(toggle.checked).toBe(true);
		expect(target.textContent).toContain('Ignore dashes and underscores');
	});

	it('only offers the switch for name-like facts and drops it when the operator cannot use it', async () => {
		renderEditor(civitaiDraft());
		await settle();
		expect(target.querySelector('[data-testid="ignore-separators"]')).toBeNull();
	});

	it('sends the preview to the instance API and lists matching names', async () => {
		renderEditor(kreaDraft());
		await advanceTimers(600);
		await settle();
		expect(urls(client.post)).toContain(`${BASE}/preview`);
		const body = client.post.mock.calls.find((c) => String(c[0]).endsWith('/preview'))![1];
		expect(body.conditions).toEqual([{ fact: 'name', operator: 'contains', value: 'krea2', ignore_separators: true }]);
		expect(body.actions).toEqual([{ action: 'assign_model_group', config: { group_id: 'g1' } }]);
		const names = [...target.querySelectorAll('[data-testid="preview-sample"] li')].map((li) => li.textContent?.trim());
		expect(names).toEqual(['krea2_dev_bf16', 'krea2_dev_fp8', 'krea2_turbo_fp8', 'krea2_edit_fp8', 'krea2_base_bf16']);
		expect(target.textContent).toContain('Also run it on the 14 existing models when I save');
		expect(allUrls().every((u) => !u.startsWith('/api/organize'))).toBe(true);
	});

	it('pages through every match with the numeric pagination', async () => {
		renderEditor(kreaDraft());
		await advanceTimers(600);
		await settle();
		client.post.mockClear();
		client.post.mockImplementation((url: string, body: { page?: number }) =>
			url.endsWith('/preview/matches') ? reply(matchesPage(body.page ?? 1)) : reply(previewData())
		);
		button(target, 'Show all 14').click();
		await settle();
		const dialog = document.body.querySelector('[data-testid="matches-dialog"]')!;
		expect(dialog).not.toBeNull();
		expect(client.post).toHaveBeenCalledWith(
			`${BASE}/preview/matches`,
			expect.objectContaining({ page: 1, page_size: 25 })
		);
		expect(dialog.querySelectorAll('[data-testid="match-row"]')).toHaveLength(25);
		expect(dialog.textContent).toContain('Showing 1-25 of 60');
		(dialog.querySelector('button[aria-label="Page 2"]') as HTMLButtonElement).click();
		await settle();
		expect(client.post).toHaveBeenLastCalledWith(`${BASE}/preview/matches`, expect.objectContaining({ page: 2 }));
		expect(dialog.textContent).toContain('krea2_p2_0');
		expect(dialog.textContent).toContain('Showing 26-50 of 60');
	});

	it('filters the matches by what would change', async () => {
		renderEditor(kreaDraft());
		await advanceTimers(600);
		await settle();
		button(target, 'Show all 14').click();
		await settle();
		const dialog = document.body.querySelector('[data-testid="matches-dialog"]')!;
		button(dialog, 'Already done').click();
		await settle();
		expect(client.post).toHaveBeenLastCalledWith(
			`${BASE}/preview/matches`,
			expect.objectContaining({ status: 'already_done', page: 1 })
		);
	});

	it('saves the rule with the group id and the separator flag to the instance API', async () => {
		client.post.mockImplementation((url: string) => {
			if (url.endsWith('/preview')) return reply(previewData({ would_change: 0 }));
			return reply(instanceRuleFixture());
		});
		renderEditor(kreaDraft());
		await advanceTimers(600);
		button(target, 'Create').click();
		await settle();
		const call = client.post.mock.calls.find((c) => String(c[0]) === `${BASE}/rules`)!;
		expect(call[1]).toEqual({
			name: 'Krea-2 models to krea-2',
			subject: 'model',
			match: 'all',
			conditions: [{ fact: 'name', operator: 'contains', value: 'krea2', ignore_separators: true }],
			actions: [{ action: 'assign_model_group', config: { group_id: 'g1' } }],
			enabled: true,
			stop_after: false
		});
	});

	it('turning the switch off removes the flag from the saved condition', async () => {
		renderEditor(kreaDraft());
		await advanceTimers(600);
		(target.querySelector('[data-testid="ignore-separators"] input') as HTMLInputElement).click();
		flushSync();
		await advanceTimers(600);
		const body = client.post.mock.calls.filter((c) => String(c[0]).endsWith('/preview')).at(-1)![1];
		expect(body.conditions).toEqual([{ fact: 'name', operator: 'contains', value: 'krea2' }]);
	});

	it('turning the switch on adds the flag to the condition', async () => {
		renderEditor(
			draftFromParts('model', {
				name: 'x',
				conditions: [{ fact: 'name', operator: 'contains', value: 'krea2' }],
				actions: [{ action: 'assign_model_group', config: { group_id: 'g1' } }]
			})
		);
		await advanceTimers(600);
		const toggle = target.querySelector('[data-testid="ignore-separators"] input') as HTMLInputElement;
		expect(toggle.checked).toBe(false);
		toggle.click();
		flushSync();
		await advanceTimers(600);
		const body = client.post.mock.calls.filter((c) => String(c[0]).endsWith('/preview')).at(-1)![1];
		expect(body.conditions).toEqual([{ fact: 'name', operator: 'contains', value: 'krea2', ignore_separators: true }]);
	});

	it('does not let an unfinished group choice be saved', async () => {
		renderEditor(
			draftFromParts('model', {
				name: 'x',
				conditions: [{ fact: 'name', operator: 'contains', value: 'krea2' }],
				actions: [{ action: 'assign_model_group', config: { group_id: null } }]
			})
		);
		await settle();
		button(target, 'Create').click();
		await settle();
		expect(target.textContent).toContain('Finish what the rule should do.');
		expect(urls(client.post)).not.toContain(`${BASE}/rules`);
	});
});

describe('instance rule editor: CivitAI example', () => {
	it('shows the description, the plugin origin, the false-positive caveat and the verified-file note', async () => {
		renderEditor(civitaiDraft());
		await settle();
		const row = target.querySelector('[data-action="civitai-provider.fetch_info"]')!;
		expect(row.querySelector('[data-testid="action-origin"]')!.textContent).toBe('civitai-provider');
		expect(row.querySelector('[data-testid="action-description"]')!.textContent).toContain('Looks the model up on CivitAI');
		expect(row.querySelector('[data-testid="action-notes"]')!.textContent).toContain('Only an exact SHA-256 match is accepted');
		expect(target.querySelector('[data-testid="rule-trigger-note"]')!.textContent).toContain('file is verified');
	});

	it('lists Set model type with the options the backend sent', async () => {
		renderEditor(
			draftFromParts('model', {
				name: 'Types',
				conditions: [],
				actions: [{ action: 'set_model_type', config: { model_type: null } }]
			})
		);
		await settle();
		expect(target.querySelector('[data-action="set_model_type"]')).not.toBeNull();
		expect(target.querySelector('[data-testid="rule-trigger-note"]')!.textContent).toContain('verified');
	});

	it('offers the four instance actions and no collection action', async () => {
		renderEditor(civitaiDraft());
		await settle();
		const labels = [...target.querySelectorAll('button')].map((b) => b.textContent?.trim());
		for (const label of ['Give a user group access', 'Add model tags', 'Set model type', 'Fetch CivitAI data']) {
			expect(labels).toContain(label);
		}
		expect(labels).not.toContain('Add to collection');
		expect(api.listModelCollections).not.toHaveBeenCalled();
	});
});
