// Typed prompt variables: the typed model + pure conversion to the wire format.
//
// Wire contract (see docs/prompts.md / expander.py): a value is a template
// string; `{a|b|c}` samples one option per image. A `choice` variable
// serializes to that grammar (mode `per-image`), a single client-rolled value
// (mode `shuffle`), or the literal text of a pinned option (mode `pin`), reusing
// choiceGroups.ts's serializer so a choice variable and a hand-typed group
// produce identical syntax. Three modes:
//   - `shuffle` (default) — roll once client-side at Generate, plain value on the wire.
//   - `pin` — always the one pinned option.
//   - `per-image` — ships `{a|b|c}`, the backend rolls independently per image.

import { serializeGroup, type ChoiceGroupSpec } from './choiceGroups';

export interface TextVariableDef {
	type: 'text';
	/** A plain value — or, if the user chooses to type it, a raw template
	 *  (e.g. hand-typed `{a|b}`). Text variables are unrestricted, unlike
	 *  choice variables, which never accept raw notation. */
	value: string;
}

export type ChoiceVariableMode = 'shuffle' | 'pin' | 'per-image';

export interface VariableOptionWhen {
	var: string;
	values: string[];
}

export interface VariableOptionObject {
	text: string;
	when?: VariableOptionWhen;
}

export type VariableOption = string | VariableOptionObject;

export interface ChoiceVariableDef {
	type: 'choice';
	options: VariableOption[];
	mode: ChoiceVariableMode;
	/** Index into `options` to pin — only meaningful (and only ever read) when
	 *  `mode === 'pin'`. Pinning is per-variable, not per-usage: the backend
	 *  binds one value per name per generation, shared by every `${name}` in
	 *  the prompt, so "pin one option" can only mean pinning the definition. */
	pinnedIndex: number | null;
}

export type VariableDef = TextVariableDef | ChoiceVariableDef;

/** Legacy shape: no `mode` field — mode was implied by whether `pinnedIndex`
 *  was set. normalizeVariableDef migrates a no-mode def to `shuffle`. */
export interface LegacyChoiceVariableDef {
	type: 'choice';
	options: VariableOption[];
	pinnedIndex: number | null;
}

export function optionText(option: VariableOption): string {
	return typeof option === 'string' ? option : option.text;
}

export function optionWhen(option: VariableOption): VariableOptionWhen | undefined {
	return typeof option === 'string' ? undefined : option.when;
}

export function whenIsBroken(when: VariableOptionWhen, variables: VariablesMap): boolean {
	const refStored = variables[when.var];
	if (refStored === undefined) return true;
	const refDef = normalizeVariableDef(refStored);
	if (refDef.type !== 'choice') return true;
	const refTexts = new Set(refDef.options.map((o) => optionText(o).trim()));
	return when.values.some((v) => !refTexts.has(v));
}

export function dependencies(name: string, variables: VariablesMap, transitive = false): string[] {
	const def = normalizeVariableDef(variables[name]);
	const direct = new Set<string>();
	if (def.type === 'choice') {
		for (const option of def.options) {
			const when = optionWhen(option);
			if (when && when.var !== name && variables[when.var] !== undefined) direct.add(when.var);
		}
	}
	if (!transitive) return [...direct];

	const seen = new Set<string>();
	const stack = [...direct];
	while (stack.length > 0) {
		const current = stack.pop() as string;
		if (seen.has(current) || current === name) continue;
		seen.add(current);
		for (const dep of dependencies(current, variables, false)) stack.push(dep);
	}
	return [...seen];
}

export function dependents(name: string, variables: VariablesMap, transitive = false): string[] {
	const direct = Object.keys(variables).filter(
		(other) => other !== name && dependencies(other, variables, false).includes(name)
	);
	if (!transitive) return direct;

	const seen = new Set<string>();
	const stack = [...direct];
	while (stack.length > 0) {
		const current = stack.pop() as string;
		if (seen.has(current) || current === name) continue;
		seen.add(current);
		for (const dep of dependents(current, variables, false)) stack.push(dep);
	}
	return [...seen];
}

export function resolveVariableOrder(variables: VariablesMap | undefined): string[] {
	if (!variables) return [];
	const order: string[] = [];
	const done = new Set<string>();
	const active = new Set<string>();

	function visit(name: string) {
		if (done.has(name) || active.has(name)) return;
		active.add(name);
		for (const dep of dependencies(name, variables!, false)) visit(dep);
		active.delete(name);
		done.add(name);
		order.push(name);
	}

	for (const name of Object.keys(variables)) visit(name);
	return order;
}

export function isOptionEligible(option: VariableOption, resolved: Record<string, string>): boolean {
	const when = optionWhen(option);
	if (!when) return true;
	const actual = resolved[when.var];
	return actual !== undefined && when.values.includes(actual);
}

export function eligibleOptions(def: ChoiceVariableDef, resolved: Record<string, string>): VariableOption[] {
	return def.options.filter((option) => optionText(option).trim().length > 0 && isOptionEligible(option, resolved));
}

/** Older sessions stored a variable as a bare string (the template itself), or
 *  a choice def with no `mode`. Normalizing either keeps saved sessions working
 *  without a migration step. */
export type StoredVariableDef = VariableDef | LegacyChoiceVariableDef | string;

export type VariablesMap = Record<string, StoredVariableDef>;

export function normalizeVariableDef(stored: StoredVariableDef | undefined): VariableDef {
	if (stored === undefined) return { type: 'text', value: '' };
	if (typeof stored === 'string') return { type: 'text', value: stored };
	if (stored.type === 'text') return stored;

	// choice — migrate a def with no `mode`.
	const mode: ChoiceVariableMode =
		'mode' in stored && stored.mode
			? stored.mode
			: stored.pinnedIndex !== null && stored.pinnedIndex !== undefined
				? 'pin'
				: 'shuffle';

	return { type: 'choice', options: stored.options, mode, pinnedIndex: stored.pinnedIndex ?? null };
}

export function createTextVariable(value: string = ''): TextVariableDef {
	return { type: 'text', value };
}

export function createChoiceVariable(options: VariableOption[] = ['', '']): ChoiceVariableDef {
	return { type: 'choice', options, mode: 'shuffle', pinnedIndex: null };
}

/**
 * The wire-format template string for one variable, WITHOUT rolling a
 * `shuffle`-mode choice — used for `text`, `pin`, and `per-image`, all
 * deterministic given only the definition. `shuffle` resolves to the grouped
 * `{a|b|c}` form here as a preview/fallback only; actual submission rolls
 * client-side via rollChoiceOption / buildVariablesForSubmit instead.
 */
export function buildVariableWireValue(def: VariableDef): string {
	if (def.type === 'text') return def.value;

	const validOptions = def.options.map((o) => optionText(o).trim()).filter((o) => o.length > 0);
	if (validOptions.length === 0) return '';

	if (def.mode === 'pin' && def.pinnedIndex !== null) {
		const pinned = def.options[def.pinnedIndex];
		const pinnedText = pinned !== undefined ? optionText(pinned).trim() : '';
		if (pinnedText.length > 0) return pinnedText;
		// Pinned index points at a blank/removed option — fall through to the
		// grouped form across whatever valid options remain rather than
		// silently resolving to nothing.
	}

	if (validOptions.length === 1) return validOptions[0];

	const spec: ChoiceGroupSpec = {
		options: validOptions.map((text) => ({ text, weight: null })),
		count: null,
		countMax: null,
		separator: null
	};
	return serializeGroup(spec);
}

export interface VariableRoll {
	optionIndex: number;
	value: string;
	rolledAt: number;
	because?: { var: string; value: string };
	eligible?: number;
	total?: number;
}

/**
 * Pick one non-blank option at random. Pure given `random` (a `[0,1)`
 * source, defaults to `Math.random` — inject a fixed sequence in tests for
 */
export function rollChoiceOption(
	def: ChoiceVariableDef,
	random: () => number = Math.random
): { index: number; value: string } | null {
	const validIndices = def.options
		.map((o, i) => ({ trimmed: optionText(o).trim(), i }))
		.filter((x) => x.trimmed.length > 0)
		.map((x) => x.i);
	if (validIndices.length === 0) return null;
	const pick = validIndices[Math.floor(random() * validIndices.length)];
	return { index: pick, value: optionText(def.options[pick]) };
}

export interface VariablesSubmitResult {
	/** The `Record<string, string>` for `GenerationRequest.variables` — every
	 *  entry normalized, resolved per its mode, and serialized. Entries that
	 *  resolve to nothing (blank text, or a choice variable with no valid
	 *  options) are omitted entirely rather than sent as `""`, so they read as
	 *  "not defined" the same way an omitted variable does. */
	wireMap: Record<string, string>;
	/** A fresh roll for every `shuffle`-mode choice variable resolved this
	 *  pass. This is RUN state, not definition state — the caller persists it
	 *  separately (e.g. `Tab.variableRolls`, not `Tab.variables`) so usage
	 *  chips can re-render showing the pick without it being mistaken for
	 *  something the user configured. */
	rolls: Record<string, VariableRoll>;
}

export interface VariablesForSubmitOptions {
	/** Injectable for deterministic tests; defaults to `Math.random`. */
	random?: () => number;
	/** Injectable for deterministic tests; defaults to `Date.now`. */
	now?: () => number;
}

/**
 * The mode-aware wire-building pass, run once per Generate click (shared by both
 * request-assembly sites — generationOrchestrator.ts `buildVariablesPayload` and
 */
export function buildVariablesForSubmit(
	variables: VariablesMap | undefined,
	options: VariablesForSubmitOptions = {}
): VariablesSubmitResult {
	const random = options.random ?? Math.random;
	const now = options.now ?? Date.now;
	const wireMap: Record<string, string> = {};
	const rolls: Record<string, VariableRoll> = {};

	if (!variables) return { wireMap, rolls };

	const resolved: Record<string, string> = {};

	for (const name of resolveVariableOrder(variables)) {
		const stored = variables[name];
		if (stored === undefined) continue;
		const def = normalizeVariableDef(stored);

		if (def.type === 'text') {
			if (def.value) wireMap[name] = def.value;
			resolved[name] = def.value.trim();
			continue;
		}

		if (def.mode === 'shuffle') {
			const validEntries = def.options
				.map((o, i) => ({ option: o, index: i, text: optionText(o).trim() }))
				.filter((entry) => entry.text.length > 0);
			if (validEntries.length === 0) continue;

			const hasConditions = validEntries.some((entry) => !!optionWhen(entry.option));
			const eligibleEntries = validEntries.filter((entry) => isOptionEligible(entry.option, resolved));

			if (eligibleEntries.length === 0) {
				if (hasConditions) {
					rolls[name] = { optionIndex: -1, value: '', rolledAt: now(), eligible: 0, total: validEntries.length };
				}
				continue;
			}

			const pick = eligibleEntries[Math.floor(random() * eligibleEntries.length)];
			wireMap[name] = pick.text;
			resolved[name] = pick.text;
			const roll: VariableRoll = { optionIndex: pick.index, value: pick.text, rolledAt: now() };
			if (hasConditions) {
				roll.eligible = eligibleEntries.length;
				roll.total = validEntries.length;
				const when = optionWhen(pick.option);
				if (when) roll.because = { var: when.var, value: resolved[when.var] ?? '' };
			}
			rolls[name] = roll;
			continue;
		}

		const value = buildVariableWireValue(def);
		if (value) wireMap[name] = value;
		if (def.mode === 'pin') resolved[name] = value;
	}

	return { wireMap, rolls };
}

/**
 * The decision behind a `${name}` usage chip's rendering: returns `null`
 * exactly when `name` has no entry in `variables` at all — the case that gets
 * warning styling and a "Create this variable" offer. A defined-but-empty
 * variable (text with `value: ''`, or a choice with no valid options yet) still
 * renders as a normal chip, since the user is mid-authoring it.
 */
export function resolveVariableChipState(name: string, variables: VariablesMap | undefined): VariableDef | null {
	if (!variables || !(name in variables)) return null;
	return normalizeVariableDef(variables[name]);
}

/**
 * Deterministic fingerprint of an entire `VariablesMap` — used by
 * InlineChipEditor.svelte to notice "the map changed externally" (a session
 * load, a pin made from a chip's own popover round-tripping back down as a
 * new prop, etc.) and remount every usage chip so stale definitions/warning
 * styling refresh. Note that `hashVariablesMap({})` is `''` — a legitimate,
 * common value (most tabs start with no variables at all), NOT a sentinel.
 */
export function hashVariablesMap(vars: VariablesMap): string {
	return Object.entries(vars)
		.map(([name, def]) => `${name}:${JSON.stringify(def)}`)
		.sort()
		.join('|');
}

export interface HashChangeStep {
	shouldRemount: boolean;
	nextHash: string;
}

/**
 * One step of the "remount on external change" state machine InlineChipEditor
 * runs on every reactive tick. `lastHash === null` means "nothing observed yet"
 * — the first observation seeds the baseline rather than counting as a change.
 * The sentinel MUST be `null`, not `''`: `hashVariablesMap({})` is `''`, so an
 * empty-variables tab (the common case) would otherwise stay stuck in the
 * uninitialized state and never remount when its first variable is defined.
 */
export function stepVariablesHash(currentHash: string, lastHash: string | null): HashChangeStep {
	if (lastHash === null) return { shouldRemount: false, nextHash: currentHash };
	return { shouldRemount: currentHash !== lastHash, nextHash: currentHash };
}
