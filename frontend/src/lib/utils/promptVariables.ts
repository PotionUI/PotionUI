// Prompt variable USAGE syntax — `${name}` — and the `$`-trigger picker.
//
// Variables are NOT defined inline in the prompt text: there is no
// `${name=value}` assignment form. `dynamicprompts`/expander.py binds them
// out of band via `SamplingContext.with_variables(...)`, fed from
// `GenerationRequest.variables` (a name -> value template map — see
// src/features/generation/dto.py and src/features/prompt/expander.py). A
// prompt only ever *references* a variable with `${name}`; the definitions
// themselves live on the tab (see stores/tabs.ts `Tab.variables`) and are
// attached to the generation request separately, never spliced into the
// prompt text.
//
// These helpers are pure: they only look at plain strings/cursor offsets and
// never mutate anything, so they're safe to call from a Svelte `$:` block.

/** Valid dynamicprompts variable name: identifier-like, no spaces/braces/`$`. */
export const VARIABLE_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

export function isValidVariableName(name: string): boolean {
	return VARIABLE_NAME_RE.test(name);
}

/** The exact text a `${name}` usage chip/insertion must produce. */
export function variableUsageSyntax(name: string): string {
	return `\${${name}}`;
}

export interface VariableTriggerMatch {
	/** Index of the triggering `$` in `text`. */
	start: number;
	/** Cursor offset (exclusive end of the typed query so far). */
	end: number;
	/** Characters typed after `$`, used to filter the picker. */
	query: string;
}

/**
 * Detect a `$name` in progress immediately before `cursorOffset`, mirroring
 * how the existing `#category` phrasebook trigger is detected. Stops
 * scanning backward at whitespace, newline, `{`, `}`, or another `$`/`#` so
 * it doesn't reach across an unrelated token.
 */
export function detectVariableTrigger(text: string, cursorOffset: number): VariableTriggerMatch | null {
	let start = cursorOffset - 1;
	while (
		start >= 0 &&
		text[start] !== '$' &&
		text[start] !== ' ' &&
		text[start] !== '\n' &&
		text[start] !== '{' &&
		text[start] !== '}' &&
		text[start] !== '#'
	) {
		start--;
	}

	if (start >= 0 && text[start] === '$') {
		const query = text.substring(start + 1, cursorOffset);
		if (/^[A-Za-z0-9_]*$/.test(query)) {
			return { start, end: cursorOffset, query };
		}
	}

	return null;
}

/** Filter + rank variable names for the picker: prefix matches first, then substring, alphabetical within each tier. */
export function filterVariableNames(names: string[], query: string): string[] {
	const q = query.toLowerCase();
	return names
		.filter((n) => n.toLowerCase().includes(q))
		.sort((a, b) => {
			const aStarts = a.toLowerCase().startsWith(q) ? 0 : 1;
			const bStarts = b.toLowerCase().startsWith(q) ? 0 : 1;
			if (aStarts !== bStarts) return aStarts - bStarts;
			return a.localeCompare(b);
		});
}

/** Replace a detected `$query` span with the full `${name}` usage text. Pure splice, like spliceGroupText. */
export function insertVariableUsage(text: string, trigger: Pick<VariableTriggerMatch, 'start' | 'end'>, name: string): string {
	return text.slice(0, trigger.start) + variableUsageSyntax(name) + text.slice(trigger.end);
}

const VARIABLE_USAGE_RE = /\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g;

export interface VariableUsageTextToken {
	type: 'text';
	raw: string;
	start: number;
	end: number;
}

export interface VariableUsageToken {
	type: 'variable';
	/** Exact source text: `${name}`. */
	raw: string;
	start: number;
	end: number;
	name: string;
}

/**
 * Tokenize `text` into an ordered, gap-free, non-overlapping list of text and
 * `${name}` usage tokens (Phase 3 — usage chips). This is its own pass, not
 * folded into choiceGroups.ts's group tokenizer: `${...}` is deliberately
 * excluded there (see parseChoiceGroupTokens) precisely so the two token
 * kinds never fight over the same `{`/`}` characters. See promptTokens.ts for
 * the function that runs both passes together over one string.
 */
export function parseVariableUsageTokens(text: string): Array<VariableUsageTextToken | VariableUsageToken> {
	const tokens: Array<VariableUsageTextToken | VariableUsageToken> = [];
	let cursor = 0;
	VARIABLE_USAGE_RE.lastIndex = 0;
	let match: RegExpExecArray | null;
	while ((match = VARIABLE_USAGE_RE.exec(text)) !== null) {
		if (match.index > cursor) {
			tokens.push({ type: 'text', raw: text.slice(cursor, match.index), start: cursor, end: match.index });
		}
		const end = match.index + match[0].length;
		tokens.push({ type: 'variable', raw: match[0], start: match.index, end, name: match[1] });
		cursor = end;
	}
	if (cursor < text.length) {
		tokens.push({ type: 'text', raw: text.slice(cursor), start: cursor, end: text.length });
	}
	return tokens;
}

/** Count of `${name}` usage OCCURRENCES in `text` (not deduped by name — two
 *  different `${mood}` spans both count) — used the same way
 *  `countChoiceGroups` is, to notice "the user just closed a `${name}`" by
 *  comparing counts before/after an edit rather than diffing text. */
export function countVariableUsages(text: string): number {
	return parseVariableUsageTokens(text).filter((t) => t.type === 'variable').length;
}

/** Every `${name}` usage in `text`, in order of first appearance, deduped. */
export function extractVariableUsages(text: string): string[] {
	const seen = new Set<string>();
	const names: string[] = [];
	VARIABLE_USAGE_RE.lastIndex = 0;
	let match: RegExpExecArray | null;
	while ((match = VARIABLE_USAGE_RE.exec(text)) !== null) {
		const name = match[1];
		if (!seen.has(name)) {
			seen.add(name);
			names.push(name);
		}
	}
	return names;
}

/**
 * Names referenced as `${name}` across `texts` that aren't in `definedNames`.
 * The backend expander binds `unknown_variable_value=""` (expander.py
 * `_base_context`), so an undefined usage doesn't fail the generation — it
 * silently expands to nothing. This is the pure detector behind the
 * non-blocking submit-time warning; it flags exactly the case that would
 * otherwise vanish without a trace.
 */
export function findUndefinedVariableUsages(texts: string[], definedNames: Iterable<string>): string[] {
	const defined = new Set(definedNames);
	const seen = new Set<string>();
	const undefinedNames: string[] = [];
	for (const text of texts) {
		for (const name of extractVariableUsages(text)) {
			if (!defined.has(name) && !seen.has(name)) {
				seen.add(name);
				undefinedNames.push(name);
			}
		}
	}
	return undefinedNames;
}
