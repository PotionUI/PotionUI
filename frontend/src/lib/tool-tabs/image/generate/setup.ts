import { getSchemaDefaults } from '$lib/form/defaults';
import type { DefaultHealth, DefaultReason, EditorTaskInfo, SetupRef, TaskDefault, TaskOffer } from '$lib/services/api/editorTasks';
import type { ResolvedSetup, SetupSource } from './generateStep';

export const DEFAULTS_NAME = 'Model defaults';

export interface SessionChoice {
	id: string;
	name: string;
	updatedAt: string | null;
	values: Record<string, unknown>;
	version?: number | null;
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
	variant: string | null;
	setup: SetupRef;
}

export function offerKey(offer: Pick<TaskOffer, 'preset_id' | 'mode' | 'variant'>): string {
	return `${offer.preset_id}\u0000${offer.mode}\u0000${offer.variant ?? ''}`;
}

export function choiceKey(choice: SetupChoice): string {
	return `${offerKey(choice)}\u0000${choice.setup.kind}\u0000${'id' in choice.setup ? choice.setup.id : ''}`;
}

export function sameChoice(a: SetupChoice | null, b: SetupChoice | null): boolean {
	return !!a && !!b && choiceKey(a) === choiceKey(b);
}

export function offerOf(offers: readonly TaskOffer[], choice: Pick<SetupChoice, 'preset_id' | 'mode' | 'variant'>): TaskOffer | null {
	return offers.find((offer) => offerKey(offer) === offerKey(choice)) ?? offers.find((offer) => offer.preset_id === choice.preset_id && offer.mode === choice.mode) ?? null;
}

interface SchemaNode {
	name?: string;
	type?: string;
	children?: SchemaNode[];
}

function walk(nodes: readonly SchemaNode[], into: string[]): void {
	for (const node of nodes) {
		if (node?.name && (node.type === 'image' || node.type === 'media')) into.push(node.name, `${node.name}_inpaint_mask`, `${node.name}__origin`);
		if (Array.isArray(node?.children)) walk(node.children, into);
	}
}

export function schemaOwnedFields(schema: { properties?: Record<string, SchemaNode> } | null | undefined): string[] {
	const owned: string[] = [];
	for (const root of Object.values(schema?.properties ?? {})) walk(root?.children ?? [], owned);
	return owned;
}

export function ownedFieldsOf(offer: TaskOffer | null, schema: { properties?: Record<string, SchemaNode> } | null | undefined): Set<string> {
	return new Set([...(offer?.owned ?? []), ...schemaOwnedFields(schema)]);
}

export function withoutOwned(values: Record<string, unknown>, owned: ReadonlySet<string>): Record<string, unknown> {
	return Object.fromEntries(Object.entries(values).filter(([key]) => !owned.has(key)));
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

export function chipLabel(offer: TaskOffer, source: SetupSource): string {
	return `${offer.preset_name} · ${offer.label ?? offer.mode_label} · ${setupName(source)}`;
}

export function layerOf(setup: SetupRef, choices: ModeChoices): Record<string, unknown> {
	if (setup.kind === 'session') return choices.sessions.find((entry) => entry.id === setup.id)?.values ?? {};
	if (setup.kind === 'formula') return choices.formulas.find((entry) => entry.id === setup.id)?.values ?? {};
	return {};
}

export function resolveSetup(input: ResolveInput): ResolvedSetup {
	const { offer, setup, schema, choices } = input;
	const owned = ownedFieldsOf(offer, schema);
	const source = sourceOf(setup, choices);
	const layer = layerOf(setup, choices);
	const defaults = getSchemaDefaults(schema as never) as Record<string, unknown>;
	const form_data = withoutOwned({ ...defaults, ...withoutOwned(layer, owned) }, owned);
	return { preset_id: offer.preset_id, mode: offer.mode, variant: offer.variant, form_data, source, label: chipLabel(offer, source) };
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
	choicesOf(offer: TaskOffer): ModeChoices;
}

export function qualifying(offers: readonly TaskOffer[]): TaskOffer[] {
	return offers.filter((offer) => offer.qualifies);
}

export function defaultsChoice(offer: TaskOffer): SetupChoice {
	return { preset_id: offer.preset_id, mode: offer.mode, variant: offer.variant, setup: { kind: 'defaults' } };
}

export function planStart(input: StartInput): StartPlan {
	const usable = qualifying(input.offers);
	if (input.picked) return { action: 'run', choice: input.picked, remembered: false };
	if (input.taskDefault) {
		const health = input.taskDefault.health;
		if (health.ok) {
			const { preset_id, mode, variant, setup } = input.taskDefault;
			return { action: 'run', choice: { preset_id, mode, variant, setup }, remembered: true };
		}
		return { action: 'picker', why: 'broken_default', notice: brokenDefaultNotice(health, input.task.label) };
	}
	if (!usable.length) return { action: 'picker', why: 'nothing_qualifies', notice: null };
	if (usable.length === 1) {
		const only = usable[0];
		const choices = input.choicesOf(only);
		if (!choices.sessions.length && !choices.formulas.length) return { action: 'run', choice: defaultsChoice(only), remembered: false };
	}
	return { action: 'picker', why: 'first_use', notice: null };
}

export interface PickerMode {
	offer: TaskOffer;
	how: string;
	disabledReason: string | null;
	options: Array<{ key: string; choice: SetupChoice; name: string; kind: 'defaults' | 'session' | 'formula'; meta: string | null }>;
}

export interface PickerGroup {
	label: string;
	modes: PickerMode[];
}

export function relativeWhen(iso: string | null, now = Date.now()): string | null {
	if (!iso) return null;
	const then = Date.parse(iso);
	if (!Number.isFinite(then)) return null;
	const days = Math.floor((now - then) / 86_400_000);
	if (days <= 0) return 'Today';
	if (days === 1) return 'Yesterday';
	if (days < 7) return `${days} days ago`;
	if (days < 14) return 'Last week';
	return new Date(then).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

export function pickerGroups(offers: readonly TaskOffer[], choicesOf: (offer: TaskOffer) => ModeChoices, now = Date.now()): PickerGroup[] {
	const usable = qualifying(offers);
	const shown = usable.length ? usable : [...offers];
	const groups = new Map<string, PickerGroup>();
	for (const offer of shown) {
		const group = groups.get(offer.preset_id) ?? { label: offer.preset_name, modes: [] };
		const choices = choicesOf(offer);
		const base = defaultsChoice(offer);
		const options: PickerMode['options'] = [{ key: choiceKey(base), choice: base, name: DEFAULTS_NAME, kind: 'defaults', meta: null }];
		for (const entry of choices.sessions) {
			const choice: SetupChoice = { ...base, setup: { kind: 'session', id: entry.id } };
			options.push({ key: choiceKey(choice), choice, name: entry.name, kind: 'session', meta: relativeWhen(entry.updatedAt, now) });
		}
		for (const entry of choices.formulas) {
			const choice: SetupChoice = { ...base, setup: { kind: 'formula', id: entry.id } };
			options.push({ key: choiceKey(choice), choice, name: entry.name, kind: 'formula', meta: null });
		}
		group.modes.push({ offer, how: howOf(offer), disabledReason: offer.qualifies ? null : offerReasonText(offer.reason), options });
		groups.set(offer.preset_id, group);
	}
	return [...groups.values()];
}
