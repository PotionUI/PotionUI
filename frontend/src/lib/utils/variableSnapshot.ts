// The generate tab's typed prompt variables, made legible to the LLM
// chat on two surfaces:
//
//   1. buildVariablesSnapshot — the compact `variables` list added to the chat
//      form-state snapshot (frontend → backend). The backend renders it into a
//      plain-language "Prompt variables" section for @form / get_form_state
//      (see src/platform/resources/prompt_variables.py). Caps mirror that
//      renderer so a padded tab can never bloat the prompt.
//
//   2. buildVariableChipTooltips — the per-name tooltip text a `${name}`
//      variable chip shows inside a chat MESSAGE (see decorateVariableChips in
//      markdown.ts). Presence of a name in the returned map is what marks it
//      "known": an unknown `${name}` stays literal text in chat.
//
// Both are pure so the snapshot shape and the tooltip copy have direct test
// coverage independent of the Svelte components that consume them.

import {
	normalizeVariableDef,
	type ChoiceVariableMode,
	type VariableDef,
	type VariablesMap,
	type VariableRoll
} from './variableDefs';

// Payload discipline for the small (26B) chat model — kept identical to the
// backend caps in src/platform/resources/prompt_variables.py.
const MAX_VARIABLES = 24;
const MAX_OPTIONS = 12;
const MAX_VALUE_CHARS = 80;

function clip(text: string, limit = MAX_VALUE_CHARS): string {
	return text.length > limit ? text.slice(0, limit) + '…' : text;
}

function validOptions(def: Extract<VariableDef, { type: 'choice' }>): string[] {
	return def.options.map((o) => o.trim()).filter((o) => o.length > 0);
}

/** One entry in the chat form-state snapshot's `variables` list. Compact by
 *  design — only the fields the backend renderer reads. */
export interface VariableSnapshotEntry {
	name: string;
	type: 'text' | 'choice';
	/** text variables only — the current value, clipped. */
	value?: string;
	/** choice variables only — non-blank options, clipped and count-capped. */
	options?: string[];
	/** choice variables only. */
	mode?: ChoiceVariableMode;
	/** choice + pin only — index into `options` (post-filter). */
	pinnedIndex?: number;
	/** choice + shuffle only — the last client-side roll's value, if present. */
	lastRoll?: string;
}

/**
 * Build the compact `variables` array for the chat snapshot. `rolls` is the
 * tab's run-state (Tab.variableRolls); a shuffle variable's last roll is
 * surfaced so the model can say "it last rolled sunlit". Entries with no
 * usable content (a choice with no valid options) are dropped.
 */
export function buildVariablesSnapshot(
	variables: VariablesMap | undefined,
	rolls?: Record<string, VariableRoll> | undefined
): VariableSnapshotEntry[] {
	if (!variables) return [];
	const out: VariableSnapshotEntry[] = [];

	for (const [name, stored] of Object.entries(variables)) {
		if (out.length >= MAX_VARIABLES) break;
		if (!name.trim()) continue;
		const def = normalizeVariableDef(stored);

		if (def.type === 'text') {
			const entry: VariableSnapshotEntry = { name, type: 'text' };
			const value = def.value.trim();
			if (value) entry.value = clip(value);
			out.push(entry);
			continue;
		}

		const opts = validOptions(def);
		if (opts.length === 0) continue; // nothing meaningful to describe yet
		const entry: VariableSnapshotEntry = {
			name,
			type: 'choice',
			options: opts.slice(0, MAX_OPTIONS).map((o) => clip(o)),
			mode: def.mode
		};
		if (def.mode === 'pin' && def.pinnedIndex !== null) {
			// Re-project the pinned index onto the filtered option list so the
			// backend can name it without seeing the blank rows we dropped.
			const pinnedText = def.options[def.pinnedIndex]?.trim();
			if (pinnedText) {
				const idx = opts.indexOf(pinnedText);
				if (idx >= 0 && idx < MAX_OPTIONS) entry.pinnedIndex = idx;
			}
		}
		const roll = rolls?.[name];
		if (def.mode === 'shuffle' && roll?.value) entry.lastRoll = clip(roll.value.trim());
		out.push(entry);
	}

	return out;
}

function describeVariable(def: VariableDef, roll: VariableRoll | undefined): string {
	if (def.type === 'text') {
		const value = def.value.trim();
		return value ? clip(value) : 'Text variable';
	}
	const opts = validOptions(def);
	const shown = opts.slice(0, MAX_OPTIONS).map((o) => clip(o));
	let listing = shown.join(', ');
	if (opts.length > MAX_OPTIONS) listing += ', …';
	let phrase: string;
	if (def.mode === 'pin') {
		const pinned = def.pinnedIndex !== null ? def.options[def.pinnedIndex]?.trim() : '';
		phrase = pinned ? `pinned to ${clip(pinned)}` : 'pinned';
	} else if (def.mode === 'per-image') {
		phrase = 're-rolls per image';
	} else {
		phrase = 'shuffles each generation';
	}
	let text = `one of ${listing} — ${phrase}`;
	if (def.mode === 'shuffle' && roll?.value) text += `; last roll: ${clip(roll.value.trim())}`;
	return text;
}

/**
 * Map of variable name → tooltip text for the chat message `${name}` chips.
 * A name present here is "known" (renders as a chip); absent names stay
 * literal text. Choice variables with no valid options are still known (the
 * user is mid-authoring), matching the editor's VariableUsageChip.
 */
export function buildVariableChipTooltips(
	variables: VariablesMap | undefined,
	rolls?: Record<string, VariableRoll> | undefined
): Record<string, string> {
	const out: Record<string, string> = {};
	if (!variables) return out;
	for (const [name, stored] of Object.entries(variables)) {
		if (!name.trim()) continue;
		out[name] = describeVariable(normalizeVariableDef(stored), rolls?.[name]);
	}
	return out;
}
