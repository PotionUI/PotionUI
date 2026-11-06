// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from './helpers/mount';
import { advanceTimers } from './helpers/settle';
import { catalogFixture, ruleFixture } from './organizeFixtures';

const api = vi.hoisted(() => ({
	previewOrganizeRule: vi.fn(),
	createOrganizeRule: vi.fn(),
	updateOrganizeRule: vi.fn(),
	applyOrganizeRuleToExisting: vi.fn(),
	getOrganizeJob: vi.fn(),
	cancelOrganizeJob: vi.fn(),
	listCollections: vi.fn(),
	getOrganizeFactOptions: vi.fn(),
	getModelById: vi.fn()
}));

vi.mock('$lib/services/api', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api')>('$lib/services/api');
	return { ...actual, api: { ...actual.api, ...api } };
});

const { default: RuleEditor } = await import('../../src/lib/components/organize/RuleEditor.svelte');
const { draftFromParts, draftFromRule } = await import('../../src/lib/organize/draft');

async function settle() {
	for (let i = 0; i < 6; i++) await advanceTimers(0);
}

let target: HTMLElement;

function render(initial: ReturnType<typeof draftFromParts>, extra: Record<string, unknown> = {}) {
	target = mountComponent(RuleEditor, { initial, catalog: catalogFixture, onBack: vi.fn(), onSaved: vi.fn(), ...extra }).target;
}

function button(label: string): HTMLButtonElement {
	const found = [...target.querySelectorAll('button')].find((b) => b.textContent?.trim() === label);
	if (!found) throw new Error(`no button ${label}`);
	return found as HTMLButtonElement;
}

function previewResult(over: Record<string, unknown> = {}) {
	return {
		success: true,
		data: { matched: 214, already_handled: 0, would_change: 198, approximate: false, sample: [], duplicates: [], ...over }
	};
}

function startDraft() {
	return draftFromParts('generation', {
		name: 'Videos',
		conditions: [{ fact: 'media_kind', operator: 'is', value: 'video' }],
		actions: [{ action: 'add_to_collection', config: { collection_name: 'Videos', create_if_missing: true } }]
	});
}

beforeEach(() => {
	vi.useFakeTimers();
	for (const fn of Object.values(api)) fn.mockReset();
	api.listCollections.mockResolvedValue({ success: true, data: { collections: [], total: 0 } });
	api.getOrganizeFactOptions.mockResolvedValue({ success: true, data: [] });
	api.previewOrganizeRule.mockResolvedValue(previewResult());
});

afterEach(() => {
	vi.useRealTimers();
});

describe('RuleEditor preview and backfill', () => {
	it('shows how many existing items match once the rule is complete', async () => {
		render(startDraft());
		await advanceTimers(600);
		expect(api.previewOrganizeRule).toHaveBeenCalledTimes(1);
		expect(api.previewOrganizeRule).toHaveBeenCalledWith({
			subject: 'generation',
			match: 'all',
			conditions: [{ fact: 'media_kind', operator: 'is', value: 'video' }],
			actions: [{ action: 'add_to_collection', config: { collection_name: 'Videos', create_if_missing: true } }],
			rule_id: null
		});
		expect(target.querySelector('[data-testid="preview-matched"]')!.textContent).toContain('214');
		expect(target.textContent).toContain('Also add the 198 existing generations when I save');
	});

	it('shows only the count and a hint until the rule has a condition, then lists matches', async () => {
		api.previewOrganizeRule.mockResolvedValue(
			previewResult({ matched: 1107, sample: [{ item_id: 'a', name: 'alpha.safetensors' }] })
		);
		render(draftFromParts('generation', { name: 'x', conditions: [], actions: [] }));
		await advanceTimers(600);
		expect(target.querySelector('[data-testid="preview-matched"]')!.textContent).toContain('1107');
		expect(target.querySelector('[data-testid="preview-sample"]')).toBeNull();
		expect(target.querySelector('[data-testid="preview-add-condition"]')).not.toBeNull();
		render(startDraft());
		await advanceTimers(600);
		expect(target.querySelector('[data-testid="preview-sample"]')).not.toBeNull();
		expect(target.querySelector('[data-testid="preview-add-condition"]')).toBeNull();
	});

	it('does not count while a condition is unfinished', async () => {
		render(
			draftFromParts('generation', {
				name: 'x',
				conditions: [{ fact: 'model', operator: 'is', value: '' }],
				actions: []
			})
		);
		await advanceTimers(600);
		expect(api.previewOrganizeRule).not.toHaveBeenCalled();
		expect(target.textContent).toContain('Finish the conditions');
	});

	it('warns about a rule with identical conditions and about approximate counts', async () => {
		api.previewOrganizeRule.mockResolvedValue(
			previewResult({ approximate: true, duplicates: [{ rule_id: 'r9', name: 'Old videos' }] })
		);
		render(startDraft());
		await advanceTimers(600);
		expect(target.textContent).toContain('About 214');
		expect(target.textContent).toContain('"Old videos"');
	});

	it('saves, starts the backfill when asked, and shows its progress', async () => {
		const saved = ruleFixture({ id: 'r1', name: 'Videos' });
		api.createOrganizeRule.mockResolvedValue({ success: true, data: saved });
		const job = { id: 'j1', rule_id: 'r1', rule_name: 'Videos', status: 'running', total: 198, processed: 50, applied: 40, run_id: null, error: null, started_at: '2026-10-05T09:12:00+00:00', finished_at: null };
		api.applyOrganizeRuleToExisting.mockResolvedValue({ success: true, data: job });
		api.getOrganizeJob.mockResolvedValue({ success: true, data: { ...job, status: 'completed', processed: 198, applied: 198, run_id: 'run1' } });
		const onSaved = vi.fn();
		render(startDraft(), { onSaved });
		await advanceTimers(600);
		(target.querySelector('[data-testid="apply-existing"]') as HTMLInputElement).click();
		flushSync();
		button('Create').click();
		await settle();
		expect(api.createOrganizeRule).toHaveBeenCalledWith({
			name: 'Videos',
			subject: 'generation',
			match: 'all',
			conditions: [{ fact: 'media_kind', operator: 'is', value: 'video' }],
			actions: [{ action: 'add_to_collection', config: { collection_name: 'Videos', create_if_missing: true } }],
			enabled: true,
			stop_after: false
		});
		expect(onSaved).toHaveBeenCalledWith(saved);
		expect(api.applyOrganizeRuleToExisting).toHaveBeenCalledWith('r1');
		const progress = target.querySelector('[data-testid="job-progress"]')!;
		expect(progress.getAttribute('data-status')).toBe('running');
		expect(progress.textContent).toContain('50 of 198');
		await advanceTimers(1100);
		expect(target.querySelector('[data-testid="job-progress"]')!.getAttribute('data-status')).toBe('completed');
		expect(target.textContent).toContain('Added 198 items to Videos');
	});

	it('renders the apply-existing option as the host switch, not a bare checkbox', async () => {
		render(startDraft());
		await advanceTimers(600);
		const control = target.querySelector('[data-testid="apply-existing"]') as HTMLInputElement;
		expect(control.getAttribute('role')).toBe('switch');
		expect(control.getAttribute('aria-label')).toMatch(/^Also add the \d+ existing/);
		expect(control.nextElementSibling?.getAttribute('aria-hidden')).toBe('true');
		expect(target.textContent).toContain('Rules only add. You can undo any run from Activity.');
		expect(control.checked).toBe(false);
		control.click();
		flushSync();
		expect(control.checked).toBe(true);
	});

	it('does not start a backfill when the box is left unchecked', async () => {
		api.createOrganizeRule.mockResolvedValue({ success: true, data: ruleFixture() });
		render(startDraft());
		await advanceTimers(600);
		button('Create').click();
		await settle();
		expect(api.createOrganizeRule).toHaveBeenCalledTimes(1);
		expect(api.applyOrganizeRuleToExisting).not.toHaveBeenCalled();
	});

	it('refuses to save without a name or an action and tells the user why', async () => {
		render(draftFromParts('generation', { name: '', conditions: [], actions: [] }));
		await settle();
		button('Create').click();
		await settle();
		expect(api.createOrganizeRule).not.toHaveBeenCalled();
		expect(target.textContent).toContain('Give the rule a name');
	});

	it('shows the server message when a save is rejected', async () => {
		api.createOrganizeRule.mockRejectedValue(
			Object.assign(new Error('bad'), {
				isAxiosError: true,
				response: { status: 422, data: { detail: { success: false, error: 'invalid_rule', message: 'The rule is not valid.', problems: [{ path: 'actions.0', code: 'collection_not_found', message: 'That collection no longer exists.' }] } } }
			})
		);
		render(startDraft());
		await advanceTimers(600);
		button('Create').click();
		await settle();
		expect(target.textContent).toContain('That collection no longer exists.');
	});

	it('updates an existing rule instead of creating one', async () => {
		const rule = ruleFixture();
		api.updateOrganizeRule.mockResolvedValue({ success: true, data: { ...rule, name: 'Clips' } });
		render(draftFromRule(rule));
		await advanceTimers(600);
		const name = target.querySelector('input[aria-label="Rule name"]') as HTMLInputElement;
		name.value = 'Clips';
		name.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		button('Save').click();
		await settle();
		expect(api.createOrganizeRule).not.toHaveBeenCalled();
		expect(api.updateOrganizeRule).toHaveBeenCalledTimes(1);
		expect(api.updateOrganizeRule.mock.calls[0][0]).toBe('r1');
		expect(api.updateOrganizeRule.mock.calls[0][1].name).toBe('Clips');
	});

	it('shows the collection a save just created instead of the placeholder', async () => {
		const created = { id: 'c9', name: 'Potion shop', parent_id: null, item_count: 0 };
		api.listCollections
			.mockResolvedValueOnce({ success: true, data: { collections: [], total: 0 } })
			.mockResolvedValue({ success: true, data: { collections: [created], total: 1 } });
		api.createOrganizeRule.mockResolvedValue({
			success: true,
			data: ruleFixture({
				actions: [{ action: 'add_to_collection', config: { collection_id: 'c9', collection_name: 'Potion shop', parent_id: null, create_if_missing: true } }]
			})
		});
		render(
			draftFromParts('generation', {
				name: 'Shop',
				conditions: [{ fact: 'media_kind', operator: 'is', value: 'video' }],
				actions: [{ action: 'add_to_collection', config: { collection_name: 'Potion shop', create_if_missing: true } }]
			})
		);
		await advanceTimers(600);
		button('Create').click();
		await settle();
		const row = target.querySelector('[data-testid="action-row"]')!;
		const select = row.querySelector('input[type="text"]') as HTMLInputElement;
		expect(select.value).toBe('Potion shop');
	});
});
