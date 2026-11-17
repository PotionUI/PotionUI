import { getSchemaDefaults } from '$lib/form/defaults';
import type { DefaultHealth, DefaultReason, EditorTaskInfo, SetupRef, TaskDefault, TaskOffer } from '$lib/services/api/editorTasks';
import type { ResolvedSetup, SetupSource } from './generateStep';

export const DEFAULTS_NAME = 'Model defaults';

export interface SessionChoice {
	id: string;
	name: string;
	createdAt: string | null;
	updatedAt: string | null;
	values: Record<string, unknown>;
	version?: number | null;
	pinned?: boolean;
}

export interface FormulaChoice {
	id: string;
	name: string;
	values: Record<string, unknown>;
}

export interface ModeChoices {
	sessions: SessionChoice[];
	formulas: FormulaChoice[];
}

export interface SetupChoice {
	preset_id: string;
	mode: string;
	setup: SetupRef;
}

export function offerKey(offer: Pick<TaskOffer, 'preset_id' | 'mode'>): string {
	return `${offer.preset_id}\u0000${offer.mode}`;
}

export function choiceKey(choice: SetupChoice): string {
	return `${offerKey(choice)}\u0000${choice.setup.kind}\u0000${'id' in choice.setup ? choice.setup.id : ''}`;
}

export function sameChoice(a: SetupChoice | null, b: SetupChoice | null): boolean {
	return !!a && !!b && choiceKey(a) === choiceKey(b);
}

export function offerOf(offers: readonly TaskOffer[], choice: Pick<SetupChoice, 'preset_id' | 'mode'>): TaskOffer | null {
	return offers.find((offer) => offerKey(offer) === offerKey(choice)) ?? null;
}

export interface ResolveInput {
	offer: TaskOffer;
	setup: SetupRef;
	schema: { properties?: Record<string, any> } | null | undefined;
	choices: ModeChoices;
}

export function sourceOf(setup: SetupRef, choices: ModeChoices): SetupSource {
	if (setup.kind === 'session') {
		const found = choices.sessions.find((entry) => entry.id === setup.id);
		return { kind: 'session', id: setup.id, name: found?.name ?? null };
	}
	if (setup.kind === 'formula') {
		const found = choices.formulas.find((entry) => entry.id === setup.id);
		return { kind: 'formula', id: setup.id, name: found?.name ?? null };
	}
	return { kind: 'defaults', id: null, name: null };
}

export function setupName(source: SetupSource): string {
	return source.name ?? DEFAULTS_NAME;
}

export function modeName(offer: Pick<TaskOffer, 'label' | 'mode_label'>): string {
	return offer.label ?? offer.mode_label;
}

export function chipLabel(offer: TaskOffer, source: SetupSource): string {
	return `${offer.preset_name} · ${modeName(offer)} · ${setupName(source)}`;
}

export function layerOf(setup: SetupRef, choices: ModeChoices): Record<string, unknown> {
	if (setup.kind === 'session') return choices.sessions.find((entry) => entry.id === setup.id)?.values ?? {};
	if (setup.kind === 'formula') return choices.formulas.find((entry) => entry.id === setup.id)?.values ?? {};
	return {};
}

export function resolveSetup(input: ResolveInput): ResolvedSetup {
	const { offer, setup, schema, choices } = input;
	const source = sourceOf(setup, choices);
	const defaults = getSchemaDefaults(schema as never) as Record<string, unknown>;
	const form_data = { ...defaults, ...layerOf(setup, choices) };
	return { preset_id: offer.preset_id, mode: offer.mode, form_data, source, label: chipLabel(offer, source) };
}

export function howOf(offer: Pick<TaskOffer, 'region_strategy'>): string {
	if (offer.region_strategy === 'mask') return 'Repaints inside the mask';
	if (offer.region_strategy === 'whole') return 'Works on the whole picture';
	return 'Works on the marked area';
}

export function offerReasonText(reason: string | null): string {
	if (reason === 'not_assigned' || reason === 'preset_unassigned') return 'Not available to your account';
	if (reason === 'plugin_disabled') return 'Turned off by its add-on';
	return 'Models not installed';
}

export function brokenDefaultNotice(health: DefaultHealth, taskLabel: string): string {
	const name = health.name ?? health.mode_label;
	const subject = name ? `Your default setup "${name}"` : 'Your default setup';
	const reason: DefaultReason = health.reason ?? 'preset_missing';
	const tail: Record<DefaultReason, string> = {
		preset_missing: 'uses a model that is no longer installed.',
		requirements_failed: 'uses a model that is no longer installed.',
		preset_unassigned: 'is no longer available to your account.',
		mode_missing: 'is no longer available.',
		offer_withdrawn: `can no longer do ${taskLabel.toLowerCase()}.`,
		session_deleted: 'was deleted.',
		formula_deleted: 'was deleted.',
		session_mismatch: 'no longer fits this model.',
		formula_mismatch: 'no longer fits this model.',
		plugin_disabled: 'was turned off by its add-on.'
	};
	return `${subject} ${tail[reason] ?? tail.preset_missing} Pick another.`;
}

export type StartPlan =
	| { action: 'run'; choice: SetupChoice; remembered: boolean }
	| { action: 'picker'; why: 'first_use' | 'broken_default' | 'nothing_qualifies'; notice: string | null };

export interface StartInput {
	task: Pick<EditorTaskInfo, 'label'>;
	offers: readonly TaskOffer[];
	taskDefault: TaskDefault | null;
	picked: SetupChoice | null;
}

export function qualifying(offers: readonly TaskOffer[]): TaskOffer[] {
	return offers.filter((offer) => offer.qualifies);
}

export function defaultsChoice(offer: Pick<TaskOffer, 'preset_id' | 'mode'>): SetupChoice {
	return { preset_id: offer.preset_id, mode: offer.mode, setup: { kind: 'defaults' } };
}

export function offersOfPreset(offers: readonly TaskOffer[], presetId: string): TaskOffer[] {
	return offers.filter((offer) => offer.preset_id === presetId);
}

export function firstChoiceOf(offers: readonly TaskOffer[], presetId: string): SetupChoice | null {
	const first = qualifying(offersOfPreset(offers, presetId))[0];
	return first ? defaultsChoice(first) : null;
}

export function planStart(input: StartInput): StartPlan {
	const usable = qualifying(input.offers);
	if (input.picked) return { action: 'run', choice: input.picked, remembered: false };
	if (input.taskDefault) {
		const health = input.taskDefault.health;
		if (health.ok) {
			const { preset_id, mode, setup } = input.taskDefault;
			return { action: 'run', choice: { preset_id, mode, setup }, remembered: true };
		}
		return { action: 'picker', why: 'broken_default', notice: brokenDefaultNotice(health, input.task.label) };
	}
	if (!usable.length) return { action: 'picker', why: 'nothing_qualifies', notice: null };
	const presets = new Set(usable.map((offer) => offer.preset_id));
	if (presets.size === 1) return { action: 'run', choice: defaultsChoice(usable[0]), remembered: false };
	return { action: 'picker', why: 'first_use', notice: null };
}
