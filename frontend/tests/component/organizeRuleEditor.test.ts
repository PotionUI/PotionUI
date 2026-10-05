// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
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

async function wait(ms = 0) {
	await new Promise((resolve) => setTimeout(resolve, ms));
	flushSync();
}

async function settle() {
	for (let i = 0; i < 6; i++) await wait();
}

let instance: ReturnType<typeof mount> | null = null;
let target: HTMLElement;

function render(initial: ReturnType<typeof draftFromParts>, extra: Record<string, unknown> = {}) {
	instance = mount(RuleEditor, {
		target,
		props: { initial, catalog: catalogFixture, onBack: vi.fn(), onSaved: vi.fn(), ...extra }
	});
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
	target = document.createElement('div');
	document.body.appendChild(target);
	for (const fn of Object.values(api)) fn.mockReset();
	api.listCollections.mockResolvedValue({ success: true, data: { collections: [], total: 0 } });
	api.getOrganizeFactOptions.mockResolvedValue({ success: true, data: [] });
	api.previewOrganizeRule.mockResolvedValue(previewResult());
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	target.remove();
});

describe('RuleEditor preview and backfill', () => {
	it('shows how many existing items match once the rule is complete', async () => {
		render(startDraft());
		await wait(700);
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

	it('does not count while a condition is unfinished', async () => {
		render(
			draftFromParts('generation', {
				name: 'x',
				conditions: [{ fact: 'model', operator: 'is', value: '' }],
				actions: []
			})
		);
		await wait(700);
		expect(api.previewOrganizeRule).not.toHaveBeenCalled();
		expect(target.textContent).toContain('Finish the conditions');
	});

	it('warns about a rule with identical conditions and about approximate counts', async () => {
		api.previewOrganizeRule.mockResolvedValue(
			previewResult({ approximate: true, duplicates: [{ rule_id: 'r9', name: 'Old videos' }] })
		);
		render(startDraft());
		await wait(700);
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
		await wait(700);
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
		await wait(1100);
		expect(target.querySelector('[data-testid="job-progress"]')!.getAttribute('data-status')).toBe('completed');
		expect(target.textContent).toContain('Added 198 items to Videos');
	});

	it('does not start a backfill when the box is left unchecked', async () => {
		api.createOrganizeRule.mockResolvedValue({ success: true, data: ruleFixture() });
		render(startDraft());
		await wait(700);
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
		await wait(700);
		button('Create').click();
		await settle();
		expect(target.textContent).toContain('That collection no longer exists.');
	});

	it('updates an existing rule instead of creating one', async () => {
		const rule = ruleFixture();
		api.updateOrganizeRule.mockResolvedValue({ success: true, data: { ...rule, name: 'Clips' } });
		render(draftFromRule(rule));
		await wait(700);
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
});
