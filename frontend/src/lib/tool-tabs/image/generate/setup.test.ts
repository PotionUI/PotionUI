import { describe, expect, it } from 'vitest';
import type { TaskOffer } from '$lib/services/api/editorTasks';
import { brokenDefaultNotice, chipLabel, defaultsChoice, offerReasonText, pickerGroups, planStart, relativeWhen, resolveSetup, sourceOf, type ModeChoices } from './setup';

function offer(over: Partial<TaskOffer> = {}): TaskOffer {
	return { preset_id: 'qwen', preset_name: 'Qwen-Image 2.1', mode: 'edit', mode_label: 'Edit', label: null, variant: null, region_strategy: 'mask', via: null, qualifies: true, reason: null, owned: [], ...over };
}

const none: ModeChoices = { sessions: [], formulas: [] };
const rich: ModeChoices = {
	sessions: [{ id: 's1', name: 'Soft portrait', updatedAt: '2026-11-14T10:00:00Z', values: { strength: 0.5, source_image: 'x', steps: 20 } }],
	formulas: [{ id: 'f1', name: 'Film look', values: { steps: 40, grain: 3 } }]
};
const schema = { properties: { main: { children: [{ name: 'strength', type: 'slider', default: 0.85 }, { name: 'steps', type: 'slider', default: 28 }, { name: 'source_image', type: 'image' }, { name: 'grain', type: 'slider', default: 0 }] } } };

describe('resolveSetup', () => {
	it('starts from the mode defaults and takes no picture field from them', () => {
		const resolved = resolveSetup({ offer: offer(), setup: { kind: 'defaults' }, schema, choices: rich });
		expect(resolved.form_data).toEqual({ strength: 0.85, steps: 28, grain: 0 });
		expect(resolved.source).toEqual({ kind: 'defaults', id: null, name: null });
		expect(resolved.label).toBe('Qwen-Image 2.1 · Edit · Model defaults');
	});

	it('lets a session win over the defaults but never brings the fields the editor owns', () => {
		const resolved = resolveSetup({ offer: offer({ owned: ['steps'] }), setup: { kind: 'session', id: 's1' }, schema, choices: rich });
		expect(resolved.form_data).toEqual({ strength: 0.5, grain: 0 });
		expect(resolved.form_data).not.toHaveProperty('source_image');
		expect(resolved.source).toEqual({ kind: 'session', id: 's1', name: 'Soft portrait' });
		expect(resolved.label).toBe('Qwen-Image 2.1 · Edit · Soft portrait');
	});

	it('applies a formula over the defaults', () => {
		const resolved = resolveSetup({ offer: offer(), setup: { kind: 'formula', id: 'f1' }, schema, choices: rich });
		expect(resolved.form_data).toMatchObject({ steps: 40, grain: 3, strength: 0.85 });
		expect(resolved.source.kind).toBe('formula');
	});

	it('copies the values, so a later change to the session cannot alter the step', () => {
		const resolved = resolveSetup({ offer: offer(), setup: { kind: 'session', id: 's1' }, schema, choices: rich });
		rich.sessions[0].values.strength = 0.1;
		expect(resolved.form_data.strength).toBe(0.5);
		rich.sessions[0].values.strength = 0.5;
	});

	it('names a source it cannot find as model defaults', () => {
		expect(sourceOf({ kind: 'session', id: 'gone' }, none)).toEqual({ kind: 'session', id: 'gone', name: null });
		expect(chipLabel(offer(), sourceOf({ kind: 'session', id: 'gone' }, none))).toContain('Model defaults');
	});
});

describe('planStart', () => {
	const task = { label: 'Edit a region' };
	const healthy = { preset_id: 'qwen', mode: 'edit', variant: null, setup: { kind: 'session' as const, id: 's1' }, health: { ok: true, reason: null, name: null, mode_label: null } };

	it('opens the picker the first time when several modes qualify', () => {
		const plan = planStart({ task, offers: [offer(), offer({ preset_id: 'flux', mode: 'inpaint' })], taskDefault: null, picked: null, choicesOf: () => none });
		expect(plan).toEqual({ action: 'picker', why: 'first_use', notice: null });
	});

	it('skips the picker only for one qualifying mode without setups or formulas', () => {
		const plan = planStart({ task, offers: [offer(), offer({ preset_id: 'flux', qualifies: false, reason: 'models_missing' })], taskDefault: null, picked: null, choicesOf: () => none });
		expect(plan).toEqual({ action: 'run', choice: defaultsChoice(offer()), remembered: false });
	});

	it('still asks when that single mode has a saved setup or a formula', () => {
		expect(planStart({ task, offers: [offer()], taskDefault: null, picked: null, choicesOf: () => rich }).action).toBe('picker');
		expect(planStart({ task, offers: [offer()], taskDefault: null, picked: null, choicesOf: () => ({ sessions: [], formulas: rich.formulas }) }).action).toBe('picker');
	});

	it('runs with a healthy default without asking', () => {
		const plan = planStart({ task, offers: [offer(), offer({ preset_id: 'flux' })], taskDefault: healthy, picked: null, choicesOf: () => none });
		expect(plan).toEqual({ action: 'run', choice: { preset_id: 'qwen', mode: 'edit', variant: null, setup: { kind: 'session', id: 's1' } }, remembered: true });
	});

	it('never substitutes for a broken default: it reopens the picker and says what went away', () => {
		const broken = { ...healthy, health: { ok: false, reason: 'session_deleted' as const, name: 'Soft portrait', mode_label: 'Edit' } };
		const plan = planStart({ task, offers: [offer()], taskDefault: broken, picked: null, choicesOf: () => none });
		expect(plan).toEqual({ action: 'picker', why: 'broken_default', notice: 'Your default setup "Soft portrait" was deleted. Pick another.' });
	});

	it('uses a setup picked for this run over the default', () => {
		const picked = defaultsChoice(offer({ preset_id: 'flux', mode: 'inpaint' }));
		const plan = planStart({ task, offers: [offer()], taskDefault: healthy, picked, choicesOf: () => none });
		expect(plan).toEqual({ action: 'run', choice: picked, remembered: false });
	});

	it('shows the greyed picker when nothing qualifies', () => {
		const plan = planStart({ task, offers: [offer({ qualifies: false })], taskDefault: null, picked: null, choicesOf: () => none });
		expect(plan).toEqual({ action: 'picker', why: 'nothing_qualifies', notice: null });
		expect(planStart({ task, offers: [], taskDefault: null, picked: null, choicesOf: () => none }).action).toBe('picker');
	});
});

describe('notices', () => {
	const health = (reason: string | null, name: string | null = 'Soft portrait') => ({ ok: false, reason: reason as never, name, mode_label: 'Edit' });
	it('words every reason in plain terms', () => {
		const expected: Record<string, string> = {
			preset_missing: 'Your default setup "Soft portrait" uses a model that is no longer installed. Pick another.',
			requirements_failed: 'Your default setup "Soft portrait" uses a model that is no longer installed. Pick another.',
			preset_unassigned: 'Your default setup "Soft portrait" is no longer available to your account. Pick another.',
			mode_missing: 'Your default setup "Soft portrait" is no longer available. Pick another.',
			offer_withdrawn: 'Your default setup "Soft portrait" can no longer do edit a region. Pick another.',
			session_deleted: 'Your default setup "Soft portrait" was deleted. Pick another.',
			formula_deleted: 'Your default setup "Soft portrait" was deleted. Pick another.',
			plugin_disabled: 'Your default setup "Soft portrait" was turned off by its add-on. Pick another.'
		};
		for (const [reason, text] of Object.entries(expected)) expect(brokenDefaultNotice(health(reason), 'Edit a region')).toBe(text);
	});

	it('falls back to the mode name and then to a generic subject', () => {
		expect(brokenDefaultNotice(health('session_deleted', null), 'Edit a region')).toContain('"Edit"');
		expect(brokenDefaultNotice({ ok: false, reason: 'session_deleted', name: null, mode_label: null }, 'x')).toBe('Your default setup was deleted. Pick another.');
	});

	it('never says session, preset or mode id', () => {
		for (const reason of ['preset_missing', 'session_deleted', 'offer_withdrawn', 'plugin_disabled']) expect(brokenDefaultNotice(health(reason), 'Edit a region')).not.toMatch(/session|preset|offer/i);
	});

	it('words offer reasons', () => {
		expect(offerReasonText('models_missing')).toBe('Models not installed');
		expect(offerReasonText('not_assigned')).toBe('Not available to your account');
	});
});

describe('pickerGroups', () => {
	it('lists the defaults first, then saved setups, then formulas, grouped by model', () => {
		const groups = pickerGroups([offer(), offer({ mode: 'edit2', mode_label: 'Edit 2' })], () => rich, Date.parse('2026-11-16T10:00:00Z'));
		expect(groups).toHaveLength(1);
		expect(groups[0].label).toBe('Qwen-Image 2.1');
		expect(groups[0].modes[0].options.map((entry) => [entry.name, entry.kind, entry.meta])).toEqual([
			['Model defaults', 'defaults', null],
			['Soft portrait', 'session', '2 days ago'],
			['Film look', 'formula', null]
		]);
		expect(groups[0].modes[0].how).toBe('Repaints inside the mask');
	});

	it('hides modes that do not qualify while one does, and greys them when none does', () => {
		const bad = offer({ preset_id: 'flux', preset_name: 'Flux Fill', qualifies: false, reason: 'not_assigned', region_strategy: 'crop' });
		expect(pickerGroups([offer(), bad], () => none).map((group) => group.label)).toEqual(['Qwen-Image 2.1']);
		const all = pickerGroups([bad], () => none);
		expect(all[0].modes[0]).toMatchObject({ disabledReason: 'Not available to your account', how: 'Works on the marked area' });
	});

	it('words relative dates', () => {
		const now = Date.parse('2026-11-16T12:00:00Z');
		expect(relativeWhen('2026-11-16T01:00:00Z', now)).toBe('Today');
		expect(relativeWhen('2026-11-15T01:00:00Z', now)).toBe('Yesterday');
		expect(relativeWhen('2026-11-08T01:00:00Z', now)).toBe('Last week');
		expect(relativeWhen(null, now)).toBeNull();
	});
});

describe('mismatch reasons', () => {
	it('words a session or formula that no longer fits', () => {
		const health = (reason: string) => ({ ok: false, reason: reason as never, name: 'Soft portrait', mode_label: 'Edit' });
		expect(brokenDefaultNotice(health('session_mismatch'), 'Edit a region')).toBe('Your default setup "Soft portrait" no longer fits this model. Pick another.');
		expect(brokenDefaultNotice(health('formula_mismatch'), 'Edit a region')).toContain('no longer fits');
	});
});
