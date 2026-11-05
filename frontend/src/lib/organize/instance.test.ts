import { describe, it, expect } from 'vitest';
import { hasOutcomes, outcomesOf, runKindLabel, runSummary, undoMessage } from './activity';
import { actionPhrases, awaitsDigest, conditionPhrases } from './sentence';
import { actionTargetFilled, canIgnoreSeparators, newAction } from './draft';
import { ruleStatusInfo } from './status';
import type { OrganizeActionSpec, OrganizeCatalog, OrganizeFactSpec, OrganizeRun } from '$lib/types/organize';

function run(over: Partial<OrganizeRun> = {}): OrganizeRun {
	return {
		id: 'r',
		rule_id: 'x',
		rule_name: 'Fetch CivitAI data',
		rule_deleted: false,
		subject: 'model',
		kind: 'backfill',
		status: 'completed',
		started_at: '2026-11-05T09:00:00+00:00',
		finished_at: null,
		matched: 31,
		applied: 19,
		undone: 0,
		can_undo: false,
		outcomes: { updated: 19, skipped: 3, waiting: 9 },
		reasons: [
			{ outcome: 'waiting', code: 'unreachable', detail: 'CivitAI could not be reached', count: 9 },
			{ outcome: 'skipped', code: 'hash_mismatch', detail: 'different file', count: 3 }
		],
		changes: { collections: [], tags: [], groups: [], other: [{ action: 'civitai-provider.fetch_info', label: 'Fetch CivitAI data', done_label: 'Fetched CivitAI data', count: 19, live: 19 }] },
		...over
	};
}

describe('run outcomes', () => {
	it('splits the reasons into waiting and skipped lines with their counts', () => {
		const o = outcomesOf(run());
		expect(o.waitingLines).toEqual([{ code: 'unreachable', label: 'CivitAI could not be reached', count: 9 }]);
		expect(o.skippedLines).toEqual([{ code: 'hash_mismatch', label: 'different file', count: 3 }]);
		expect([o.updated, o.skipped, o.waiting]).toEqual([19, 3, 9]);
		expect(hasOutcomes(run())).toBe(true);
	});

	it('has no outcomes for a user run that carries none', () => {
		const plain = run({ outcomes: undefined, reasons: undefined, subject: 'generation' });
		expect(hasOutcomes(plain)).toBe(false);
		expect(outcomesOf(plain).updated).toBe(19);
	});

	it('words model runs for models and group runs for the group', () => {
		expect(runSummary(run())).toEqual(['Fetched CivitAI data for 19 models']);
		expect(runKindLabel(run())).toBe('Existing models');
		expect(runKindLabel(run({ kind: 'live' }))).toBe('New models');
		const grp = run({ changes: { collections: [], tags: [], groups: [{ id: 'g', name: 'krea-2', count: 1, live: 1 }], other: [] } });
		expect(runSummary(grp)).toEqual(['Gave krea-2 access to 1 model']);
		expect(undoMessage(grp)).toContain('the group stays');
	});
});

describe('instance sentences and drafts', () => {
	const catalog = {
		actions: [{ key: 'civitai-provider.fetch_info', label: 'Fetch CivitAI data', needs: ['digest'] }]
	} as unknown as OrganizeCatalog;

	it('describes the instance actions in plain words', () => {
		expect(
			actionPhrases(
				[
					{ action: 'assign_model_group', config: { group_id: 'g1' } },
					{ action: 'add_model_tags', config: { tags: ['a', 'b'] } },
					{ action: 'set_model_type', config: { model_type: 'lora' } },
					{ action: 'civitai-provider.fetch_info', config: {} }
				],
				catalog,
				{},
				{ g1: 'krea-2' }
			)
		).toEqual(['give krea-2 access', 'add tags a, b', 'set the type to lora', 'fetch CivitAI data']);
	});

	it('knows an action that waits for the verified file', () => {
		expect(awaitsDigest([{ action: 'civitai-provider.fetch_info' }], catalog)).toBe(true);
		expect(awaitsDigest([{ action: 'add_to_collection' }], catalog)).toBe(false);
	});

	it('mentions ignoring dashes and underscores on a condition', () => {
		const spec = { key: 'name', label: 'Name', kind: 'text', operators: ['contains'] } as unknown as OrganizeFactSpec;
		const cat = { facts: [spec], operators: { contains: 'contains' } } as unknown as OrganizeCatalog;
		expect(conditionPhrases([{ fact: 'name', operator: 'contains', value: 'krea2', ignore_separators: true }], cat, { models: {}, options: {} })).toEqual([
			'Name contains krea2 (ignoring dashes and underscores)'
		]);
	});

	it('only allows the separator switch on facts that support it and text operators', () => {
		const spec = { ignore_separators: true } as OrganizeFactSpec;
		expect(canIgnoreSeparators(spec, 'contains')).toBe(true);
		expect(canIgnoreSeparators(spec, 'is_any_of')).toBe(false);
		expect(canIgnoreSeparators({} as OrganizeFactSpec, 'contains')).toBe(false);
	});

	it('requires the group, the tags and the type before an instance action is complete', () => {
		expect(actionTargetFilled({ uid: 'a', action: 'assign_model_group', config: { group_id: null } })).toBe(false);
		expect(actionTargetFilled({ uid: 'a', action: 'assign_model_group', config: { group_id: 'g1' } })).toBe(true);
		expect(actionTargetFilled({ uid: 'a', action: 'add_model_tags', config: { tags: [] } })).toBe(false);
		expect(actionTargetFilled({ uid: 'a', action: 'set_model_type', config: { model_type: '' } })).toBe(false);
		expect(actionTargetFilled({ uid: 'a', action: 'civitai-provider.fetch_info', config: {} })).toBe(true);
	});

	it('starts a group action without a group chosen', () => {
		const spec = { key: 'assign_model_group', config_schema: [{ key: 'group_id', kind: 'user_group', label: 'g' }] } as unknown as OrganizeActionSpec;
		expect(newAction(spec).config).toEqual({ group_id: null });
	});

	it('explains why a rule was paused for a missing group or provider', () => {
		expect(ruleStatusInfo({ status: 'paused', paused_reason: 'group_missing', issues: [] }).notice).toContain('user group is gone');
		expect(ruleStatusInfo({ status: 'paused', paused_reason: 'provider_unavailable', issues: [] }).notice).toContain('not set up');
	});
});
