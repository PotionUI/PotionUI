import { describe, expect, it } from 'vitest';
import type { TaskOffer } from '$lib/services/api/editorTasks';
import { brokenDefaultNotice, chipLabel, defaultsChoice, firstChoiceOf, offerKey, offerOf, offerReasonText, planStart, resolveSetup, sameChoice, sourceOf, type ModeChoices } from './setup';

function offer(over: Partial<TaskOffer> = {}): TaskOffer {
	return { preset_id: 'qwen', preset_name: 'Qwen-Image 2.1', mode: 'editor:edit', from_mode: 'edit', mode_label: 'Edit', label: null, short_description: null, order: 0, region_strategy: 'mask', via: null, qualifies: true, reason: null, ...over };
}

const none: ModeChoices = { sessions: [], formulas: [] };
const rich: ModeChoices = {
	sessions: [{ id: 's1', name: 'Soft portrait', createdAt: '2026-11-13T10:00:00Z', updatedAt: '2026-11-14T10:00:00Z', values: { strength: 0.5, steps: 20 } }],
	formulas: [{ id: 'f1', name: 'Film look', values: { steps: 40, grain: 3 } }]
};
const schema = { properties: { main: { children: [{ name: 'strength', type: 'slider', default: 0.85 }, { name: 'steps', type: 'slider', default: 28 }, { name: 'grain', type: 'slider', default: 0 }] } } };

describe('resolveSetup', () => {
	it('starts from the defaults of the form the server serves for the editor key', () => {
		const resolved = resolveSetup({ offer: offer(), setup: { kind: 'defaults' }, schema, choices: rich });
		expect(resolved).toMatchObject({ preset_id: 'qwen', mode: 'editor:edit' });
		expect(resolved).not.toHaveProperty('variant');
		expect(resolved.form_data).toEqual({ strength: 0.85, steps: 28, grain: 0 });
		expect(resolved.source).toEqual({ kind: 'defaults', id: null, name: null });
		expect(resolved.label).toBe('Qwen-Image 2.1 · Edit · Model defaults');
	});

	it('lets a session win over the defaults and keeps every value as saved', () => {
		const resolved = resolveSetup({ offer: offer(), setup: { kind: 'session', id: 's1' }, schema, choices: rich });
		expect(resolved.form_data).toEqual({ strength: 0.5, steps: 20, grain: 0 });
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
	const healthy = { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session' as const, id: 's1' }, health: { ok: true, reason: null, name: null, mode_label: null } };
	const flux = (over: Partial<TaskOffer> = {}) => offer({ preset_id: 'flux', preset_name: 'Flux Fill', mode: 'editor:fill', from_mode: 'inpaint', ...over });

	it('opens the preset picker the first time when several presets qualify', () => {
		const plan = planStart({ task, offers: [offer(), flux()], taskDefault: null, picked: null });
		expect(plan).toEqual({ action: 'picker', why: 'first_use', notice: null });
	});

	it('preselects silently when one preset qualifies, even if it has saved setups', () => {
		const plan = planStart({ task, offers: [offer(), flux({ qualifies: false, reason: 'models_missing' })], taskDefault: null, picked: null });
		expect(plan).toEqual({ action: 'run', choice: defaultsChoice(offer()), remembered: false });
	});

	it('preselects the first mode of the one preset that qualifies', () => {
		const second = offer({ mode: 'editor:edit2', order: 1 });
		const plan = planStart({ task, offers: [offer(), second], taskDefault: null, picked: null });
		expect(plan).toEqual({ action: 'run', choice: defaultsChoice(offer()), remembered: false });
	});

	it('runs with a healthy default without asking', () => {
		const plan = planStart({ task, offers: [offer(), flux()], taskDefault: healthy, picked: null });
		expect(plan).toEqual({ action: 'run', choice: { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session', id: 's1' } }, remembered: true });
	});

	it('never substitutes for a broken default: it leaves nothing picked and says what went away', () => {
		const broken = { ...healthy, health: { ok: false, reason: 'session_deleted' as const, name: 'Soft portrait', mode_label: 'Edit' } };
		const plan = planStart({ task, offers: [offer()], taskDefault: broken, picked: null });
		expect(plan).toEqual({ action: 'picker', why: 'broken_default', notice: 'Your default setup "Soft portrait" was deleted. Pick another.' });
	});

	it('uses a setup picked for this run over the default', () => {
		const picked = defaultsChoice(flux());
		const plan = planStart({ task, offers: [offer()], taskDefault: healthy, picked });
		expect(plan).toEqual({ action: 'run', choice: picked, remembered: false });
	});

	it('asks for a pick when nothing qualifies', () => {
		const plan = planStart({ task, offers: [offer({ qualifies: false })], taskDefault: null, picked: null });
		expect(plan).toEqual({ action: 'picker', why: 'nothing_qualifies', notice: null });
		expect(planStart({ task, offers: [], taskDefault: null, picked: null }).action).toBe('picker');
	});
});

describe('choices', () => {
	it('keys an offer by preset and editor mode only', () => {
		expect(offerKey(offer())).toBe(offerKey({ preset_id: 'qwen', mode: 'editor:edit' }));
		expect(offerKey(offer())).not.toBe(offerKey(offer({ mode: 'editor:other' })));
	});

	it('finds the offer of a choice and never matches a bare generation mode name', () => {
		expect(offerOf([offer()], { preset_id: 'qwen', mode: 'editor:edit' })?.from_mode).toBe('edit');
		expect(offerOf([offer()], { preset_id: 'qwen', mode: 'edit' })).toBeNull();
	});

	it('compares choices by preset, mode and source', () => {
		const base = defaultsChoice(offer());
		expect(sameChoice(base, defaultsChoice(offer()))).toBe(true);
		expect(sameChoice(base, { ...base, setup: { kind: 'session', id: 's1' } })).toBe(false);
	});

	it('takes the first usable mode of a preset', () => {
		const offers = [offer({ qualifies: false, reason: 'models_missing' }), offer({ mode: 'editor:edit2' })];
		expect(firstChoiceOf(offers, 'qwen')).toEqual(defaultsChoice(offer({ mode: 'editor:edit2' })));
		expect(firstChoiceOf([offer({ qualifies: false })], 'qwen')).toBeNull();
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

describe('mismatch reasons', () => {
	it('words a session or formula that no longer fits', () => {
		const health = (reason: string) => ({ ok: false, reason: reason as never, name: 'Soft portrait', mode_label: 'Edit' });
		expect(brokenDefaultNotice(health('session_mismatch'), 'Edit a region')).toBe('Your default setup "Soft portrait" no longer fits this model. Pick another.');
		expect(brokenDefaultNotice(health('formula_mismatch'), 'Edit a region')).toContain('no longer fits');
	});
});
