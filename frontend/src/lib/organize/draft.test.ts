import { describe, it, expect, vi } from 'vitest';
import { catalogFixture, ruleFixture } from '../../../tests/component/organizeFixtures';
import {
	coerceValue,
	defaultValue,
	draftFromRule,
	draftProblems,
	draftSignature,
	draftToInput,
	effectiveKind,
	emptyDraft,
	newAction,
	newCondition,
	describeValue
} from './draft';
import { actionPhrases, conditionPhrases, ruleSentence } from './sentence';
import { templatesFor } from './templates';
import { ruleStatusInfo, attentionCount } from './status';
import { moveItem } from './reorder';
import { jobIsActive, jobPercent } from './jobs';
import { runSummary, undoMessage } from './activity';
import { organizeHref, slugFromSubject, subjectFromSlug } from './subjects';

vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}) }));

const facts = (key: string) => catalogFixture.facts.find((f) => f.key === key)!;

describe('fact kinds', () => {
	it('treats an unknown plugin kind as text', () => {
		expect(effectiveKind(facts('tagger.mood'))).toBe('text');
		expect(effectiveKind(facts('model'))).toBe('model_ref');
		expect(effectiveKind(undefined)).toBe('text');
	});

	it('builds typed defaults from the catalog', () => {
		expect(defaultValue('model_ref', 'is')).toBe('');
		expect(defaultValue('model_ref', 'is_any_of')).toEqual([]);
		expect(defaultValue('size', 'is', facts('resolution'))).toEqual({ width: 1024, height: 1024 });
		expect(defaultValue('tag_list', 'has')).toEqual([]);
		expect(defaultValue('bool', 'is')).toBe(true);
		expect(newCondition(facts('media_kind'))).toMatchObject({ fact: 'media_kind', operator: 'is', value: '' });
	});

	it('moves values between single and list operators without losing the pick', () => {
		expect(coerceValue('enum', 'is_any_of', 'video')).toEqual(['video']);
		expect(coerceValue('enum', 'is', ['video', 'image'])).toBe('video');
		expect(coerceValue('model_ref', 'is_any_of', '')).toEqual([]);
	});
});

describe('draft', () => {
	it('round trips a rule without client ids', () => {
		const rule = ruleFixture();
		const input = draftToInput(draftFromRule(rule));
		expect(input).toEqual({
			name: 'Videos',
			subject: 'generation',
			match: 'all',
			conditions: rule.conditions,
			actions: rule.actions,
			enabled: true,
			stop_after: false
		});
		expect(JSON.stringify(input)).not.toContain('uid');
	});

	it('tracks changes with a signature that ignores client ids', () => {
		const a = draftFromRule(ruleFixture());
		const b = draftFromRule(ruleFixture());
		expect(draftSignature(a)).toBe(draftSignature(b));
		b.name = 'Other';
		expect(draftSignature(a)).not.toBe(draftSignature(b));
	});

	it('reports what is missing in plain words', () => {
		const draft = emptyDraft('generation');
		expect(draftProblems(draft, catalogFixture)).toEqual(['Give the rule a name.', 'Choose what the rule should do.']);
		draft.name = 'x';
		draft.actions.push(newAction(catalogFixture.actions[0]));
		expect(draftProblems(draft, catalogFixture)).toEqual(['Finish what the rule should do.']);
		draft.actions[0].config.collection_name = 'Videos';
		draft.conditions.push(newCondition(facts('model')));
		expect(draftProblems(draft, catalogFixture)).toEqual(['Finish every condition.']);
		draft.conditions[0].value = 'm1';
		expect(draftProblems(draft, catalogFixture)).toEqual([]);
	});
});

describe('sentences', () => {
	const labels = { models: { m1: 'Krea-2 Turbo' }, options: {} };

	it('describes values using catalog labels and model names', () => {
		expect(describeValue(facts('model'), 'm1', labels)).toBe('Krea-2 Turbo');
		expect(describeValue(facts('media_kind'), ['video', 'image'], labels)).toBe('Video, Image');
		expect(describeValue(facts('resolution'), { width: 1344, height: 768 }, labels)).toBe('1344 x 768');
	});

	it('names mode values the catalog does not list with their plain mode name', () => {
		const mode = { ...facts('media_kind'), key: 'mode', options: [{ value: 'txt2img', label: 'Text to Image' }] };
		expect(describeValue(mode, ['txt2img', 'video_upscale'], labels)).toBe('Text to Image, Video Upscale');
		expect(describeValue(facts('media_kind'), 'video_upscale', labels)).toBe('video_upscale');
	});

	it('builds the rule sentence', () => {
		const rule = ruleFixture({
			match: 'any',
			conditions: [
				{ fact: 'model', operator: 'is', value: 'm1' },
				{ fact: 'media_kind', operator: 'is', value: 'video' }
			]
		});
		expect(conditionPhrases(rule.conditions, catalogFixture, labels)).toEqual(['Model is Krea-2 Turbo', 'Media kind is Video']);
		expect(actionPhrases(rule.actions, catalogFixture, { c1: 'Renamed' })).toEqual(['add to Renamed']);
		expect(actionPhrases([{ action: 'add_tags', config: { tags: ['a', 'b'] } }], catalogFixture)).toEqual(['add tags a, b']);
		expect(ruleSentence(rule, catalogFixture, labels).join).toBe('or');
	});
});

describe('templates', () => {
	it('only offers templates whose facts exist for the subject', () => {
		expect(templatesFor('generation', catalogFixture).map((t) => t.key)).toContain('gen-videos');
		expect(templatesFor('model', catalogFixture)).toEqual([]);
		const trimmed = { ...catalogFixture, facts: catalogFixture.facts.filter((f) => f.key !== 'aspect') };
		expect(templatesFor('generation', trimmed).map((t) => t.key)).not.toContain('gen-portraits');
	});
});

describe('status and ordering', () => {
	it('explains pauses', () => {
		expect(ruleStatusInfo(ruleFixture({ status: 'paused', paused_reason: 'collection_missing' })).notice).toContain('collection is gone');
		const needs = ruleStatusInfo(ruleFixture({ status: 'needs_attention', issues: [{ code: 'model_missing', message: 'Krea is gone', blocking: true }] }));
		expect(needs).toMatchObject({ label: 'Needs attention', notice: 'Krea is gone' });
		expect(ruleStatusInfo(ruleFixture()).label).toBeNull();
		expect(attentionCount([{ status: 'paused' }, { status: 'active' }, { status: 'needs_attention' }])).toBe(2);
	});

	it('moves items', () => {
		expect(moveItem(['a', 'b', 'c'], 0, 2)).toEqual(['b', 'c', 'a']);
		expect(moveItem(['a', 'b', 'c'], 2, 5)).toEqual(['a', 'b', 'c']);
	});

	it('maps subjects and links', () => {
		expect(subjectFromSlug('uploads')).toBe('upload');
		expect(subjectFromSlug('nonsense')).toBe('generation');
		expect(slugFromSubject('model')).toBe('models');
		expect(organizeHref('upload', { rule: 'new', view: null })).toBe('/auto-organize?subject=uploads&rule=new');
	});
});

describe('jobs and activity', () => {
	it('computes progress', () => {
		expect(jobIsActive({ status: 'queued' })).toBe(true);
		expect(jobIsActive({ status: 'completed' })).toBe(false);
		expect(jobPercent({ status: 'running', processed: 50, total: 200 })).toBe(25);
		expect(jobPercent({ status: 'running', processed: 0, total: 0 })).toBe(0);
		expect(jobPercent({ status: 'completed', processed: 1, total: 2 })).toBe(100);
	});

	it('summarises a run and warns what undo does', () => {
		const run = {
			id: 'r',
			rule_id: 'x',
			rule_name: 'Krea',
			rule_deleted: false,
			subject: 'generation',
			kind: 'live',
			status: 'completed',
			started_at: '',
			finished_at: null,
			matched: 1,
			applied: 1,
			undone: 0,
			can_undo: true,
			changes: { collections: [{ id: 'c', name: 'Landscapes', count: 1, created: false }], tags: [], other: [{ action: 'p.x', label: 'Detect', count: 3 }] }
		} as never;
		expect(runSummary(run)).toEqual(['Added 1 item to Landscapes', 'Detect: 3 items']);
		expect(undoMessage(run)).toContain('the collection stays');
	});
});
