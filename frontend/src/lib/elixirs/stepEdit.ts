import type { GenerationTab } from '$lib/types/tabs';
import type { SessionData } from '$lib/types/api';
import { buildSessionRestoreTabPatch } from '$lib/utils/sessionRestore';
import { effectiveSettings, sessionMode } from './follow';
import { formOverrides, nodeLabel } from './model';
import type { ElixirDefinition, ElixirNode, FollowedState, NodeOverrides, PresetIndex, SessionSettings } from './types';

export const STEP_KEYS = [
	'prompt',
	'negativePrompt',
	'promptSegments',
	'negativePromptSegments',
	'promptTabs',
	'activePromptTab',
	'promptRelay',
	'variables',
	'seed',
	'videoDirector',
	'musicDirector'
] as const;

export const SETTLE_MS = 900;

export type StepValues = Record<string, unknown>;

export interface StepLoaded {
	values: StepValues;
	form: Record<string, unknown>;
	capturedAt: number;
}

export interface StepOption {
	id: string;
	label: string;
	detail: string;
	follow: boolean;
}

function isEmpty(value: unknown): boolean {
	if (value === undefined || value === null || value === '') return true;
	if (Array.isArray(value)) return value.length === 0;
	if (typeof value === 'object') return Object.keys(value as object).length === 0;
	return false;
}

export function sameValue(left: unknown, right: unknown): boolean {
	if (isEmpty(left) && isEmpty(right)) return true;
	return JSON.stringify(left ?? null) === JSON.stringify(right ?? null);
}

function formOf(tab: Pick<GenerationTab, 'formData'>): Record<string, unknown> {
	return { ...(tab.formData ?? {}) };
}

export function pickStepValues(tab: Partial<GenerationTab>): StepValues {
	const values: StepValues = {};
	for (const key of STEP_KEYS) values[key] = (tab as Record<string, unknown>)[key];
	return values;
}

export function captureLoaded(tab: GenerationTab, now: number): StepLoaded | null {
	if (!tab.formPublished) return null;
	return { values: pickStepValues(tab), form: formOf(tab), capturedAt: now };
}

export function observeLoaded(loaded: StepLoaded | null, tab: GenerationTab, now: number, settleMs = SETTLE_MS): StepLoaded | null {
	if (!loaded) return captureLoaded(tab, now);
	if (now - loaded.capturedAt >= settleMs) return loaded;
	const next = captureLoaded(tab, now);
	return next ? { ...next, capturedAt: loaded.capturedAt } : loaded;
}

const GROUPS: Record<string, string> = {
	promptSegments: 'prompt',
	promptTabs: 'prompt',
	activePromptTab: 'prompt',
	negativePromptSegments: 'negativePrompt'
};

export function changedKeys(tab: GenerationTab, loaded: StepLoaded | null): string[] {
	if (!loaded) return [];
	const changed = new Set<string>();
	for (const key of STEP_KEYS) {
		if (!sameValue((tab as unknown as Record<string, unknown>)[key], loaded.values[key])) changed.add(GROUPS[key] ?? key);
	}
	const form = formOf(tab);
	const names = new Set([...Object.keys(form), ...Object.keys(loaded.form)]);
	for (const name of names) {
		if (!sameValue(form[name], loaded.form[name])) changed.add(`formData.${name}`);
	}
	return [...changed];
}

export function editCount(tab: GenerationTab, loaded: StepLoaded | null): number {
	return changedKeys(tab, loaded).length;
}

export function isDirty(tab: GenerationTab, loaded: StepLoaded | null): boolean {
	return editCount(tab, loaded) > 0;
}

export function stepOverrides(node: ElixirNode, tab: GenerationTab, loaded: StepLoaded | null): NodeOverrides {
	if (!loaded) return { ...(node.overrides ?? {}) };
	let overrides: NodeOverrides = { ...(node.overrides ?? {}) };
	const base = (node.session_snapshot ?? {}) as Record<string, unknown>;
	for (const key of STEP_KEYS) {
		const value = (tab as unknown as Record<string, unknown>)[key];
		if (sameValue(value, loaded.values[key])) continue;
		if (sameValue(value, base[key])) delete overrides[key];
		else (overrides as Record<string, unknown>)[key] = value;
	}
	overrides = formOverrides(overrides, node.session_snapshot?.formData, loaded.form, formOf(tab));
	return overrides;
}

export function stepTabPatch(node: ElixirNode, settings: SessionSettings | null): Partial<GenerationTab> {
	const effective = settings ?? {};
	return {
		...buildSessionRestoreTabPatch(effective as unknown as SessionData),
		selectedPreset: node.preset_id ?? null,
		selectedMode: node.mode ?? null,
		selectedVariant: node.form_name ?? (effective.selectedVariant as string | null | undefined) ?? null,
		selectedSessionId: null,
		formPublished: false,
		formBaselineSignature: undefined,
		savedSessionSignature: undefined,
		sessionBaselineAwaitingFormNormalization: false
	};
}

export function stepSettings(node: ElixirNode, followed: FollowedState | undefined): SessionSettings | null {
	return effectiveSettings(node, followed);
}

export function nodeFingerprint(node: ElixirNode | null | undefined): string {
	if (!node) return '';
	const { position: _position, title: _title, ...rest } = node;
	return JSON.stringify(rest);
}

export function canEditStep(node: ElixirNode | null | undefined): boolean {
	return !!node && node.kind === 'preset' && !!node.preset_id && !!node.mode;
}

export function stepOptions(definition: ElixirDefinition | null, order: string[], presetIndex: PresetIndex): StepOption[] {
	if (!definition) return [];
	const options: StepOption[] = [];
	for (const id of order) {
		const node = definition.nodes.find((item) => item.id === id);
		if (!node || !canEditStep(node)) continue;
		const entry = presetIndex.get(`${node.preset_id}::${node.mode}`);
		options.push({
			id,
			label: nodeLabel(node, presetIndex),
			detail: entry ? `${entry.preset_name} · ${entry.mode_label}` : `${node.preset_id} · ${node.mode}`,
			follow: sessionMode(node) === 'follow'
		});
	}
	return options;
}

export function stepIndex(options: StepOption[], nodeId: string | null): number {
	return options.findIndex((option) => option.id === nodeId);
}

export function stepTitle(elixirName: string, stepLabel: string | null): string {
	return stepLabel ? `${elixirName} › ${stepLabel}` : elixirName;
}
