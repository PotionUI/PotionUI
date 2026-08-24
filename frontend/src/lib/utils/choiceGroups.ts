// Dynamic Prompts "choice group" syntax — {a|b|c} — parsing and serialization.
//
// Grammar this implements (see docs/prompts.md, backed by
// src/features/prompt/expander.py which samples via the `dynamicprompts`
// library):
//
//   {a|b|c}                pick one at random
//   {0.5::a|0.3::b|c}      weighted pick; an omitted weight defaults to 1
//   {2$$a|b|c}             pick two, without replacement
//   {2$$ and $$a|b|c}      pick two, joined by " and "
//   {1-2$$a|b|c}           pick between one and two
//
// The chip editor is a VIEW over the raw prompt text, never a second source
// of truth: a group's on-screen chip is produced by parsing the substring
// that already sits in the textarea/contenteditable value, and any edit made
// through the chip's popover is written back by re-serializing straight into
// that same span. Untouched text is never touched — pure functions only, no
// id minting, no hidden state.

export interface ChoiceOption {
	/** Option text. May itself contain literal `{`/`}` from a nested group —
	 *  nested groups are preserved verbatim as text and are not separately
	 *  editable. */
	text: string;
	/** `null` means "no explicit weight" (dynamicprompts default: 1). */
	weight: number | null;
}

export interface ChoiceGroupSpec {
	options: ChoiceOption[];
	/** Single/min pick count. `null` means no `N$$` prefix at all (implicit 1). */
	count: number | null;
	/** Present only for the `{min-max$$...}` range form. */
	countMax: number | null;
	/** Present only when an explicit `$$sep$$` was given. */
	separator: string | null;
}

export interface GroupToken {
	type: 'group';
	/** Exact source text, including the surrounding braces. */
	raw: string;
	start: number;
	end: number;
	spec: ChoiceGroupSpec;
}

export interface TextToken {
	type: 'text';
	raw: string;
	start: number;
	end: number;
}

export type PromptToken = GroupToken | TextToken;

/**
 * Find the index of the `{` matching the `}` implied to be at `openIndex`'s
 * balanced close, scanning forward from `openIndex + 1`. Depth-aware, so a
 * nested `{...}` inside an option doesn't terminate the outer group early.
 * Returns -1 if unbalanced.
 */
function findMatchingClose(text: string, openIndex: number): number {
	let depth = 1;
	for (let i = openIndex + 1; i < text.length; i++) {
		if (text[i] === '{') depth++;
		else if (text[i] === '}') {
			depth--;
			if (depth === 0) return i;
		}
	}
	return -1;
}

/** Split `inner` on top-level `|` only — a `|` nested inside a `{...}` option doesn't split. */
function splitTopLevel(inner: string, sep: string): string[] {
	const parts: string[] = [];
	let depth = 0;
	let current = '';
	for (let i = 0; i < inner.length; i++) {
		const ch = inner[i];
		if (ch === '{') depth++;
		else if (ch === '}') depth = Math.max(0, depth - 1);
		if (ch === sep && depth === 0) {
			parts.push(current);
			current = '';
		} else {
			current += ch;
		}
	}
	parts.push(current);
	return parts;
}

const OPTION_WEIGHT_RE = /^\s*(\d*\.?\d+)::([\s\S]*)$/;
const QUANTIFIER_RE = /^(\d+)(?:-(\d+))?\$\$([\s\S]*)$/;

/** Parse the substring between `{` and `}` (braces excluded). `null` = malformed. */
export function parseGroupInner(inner: string): ChoiceGroupSpec | null {
	let count: number | null = null;
	let countMax: number | null = null;
	let separator: string | null = null;
	let optionsStr = inner;

	const qMatch = QUANTIFIER_RE.exec(inner);
	if (qMatch) {
		count = parseInt(qMatch[1], 10);
		countMax = qMatch[2] !== undefined ? parseInt(qMatch[2], 10) : null;
		const remainder = qMatch[3];
		const sepIdx = remainder.indexOf('$$');
		if (sepIdx !== -1) {
			separator = remainder.substring(0, sepIdx);
			optionsStr = remainder.substring(sepIdx + 2);
		} else {
			optionsStr = remainder;
		}
	}

	if (optionsStr.length === 0) return null;

	const pieces = splitTopLevel(optionsStr, '|');
	if (pieces.length === 0 || pieces.every((p) => p.trim().length === 0)) return null;

	const options: ChoiceOption[] = pieces.map((piece) => {
		const wMatch = OPTION_WEIGHT_RE.exec(piece);
		if (wMatch) {
			return { weight: parseFloat(wMatch[1]), text: wMatch[2] };
		}
		return { weight: null, text: piece };
	});

	return { options, count, countMax, separator };
}

/** Reconstruct exact `{...}` source text from a structured spec. */
export function serializeGroup(spec: ChoiceGroupSpec): string {
	const optionsStr = spec.options
		.map((o) => (o.weight !== null && o.weight !== 1 ? `${formatWeight(o.weight)}::${o.text}` : o.text))
		.join('|');

	let prefix = '';
	if (spec.count !== null) {
		const countStr = spec.countMax !== null ? `${spec.count}-${spec.countMax}` : `${spec.count}`;
		const sepStr = spec.separator !== null ? `${spec.separator}$$` : '';
		prefix = `${countStr}$$${sepStr}`;
	}

	return `{${prefix}${optionsStr}}`;
}

function formatWeight(weight: number): string {
	// Avoid float noise (0.30000000000000004) without hiding real precision.
	return String(Math.round(weight * 1e6) / 1e6);
}

/**
 * Tokenize `text` into an ordered, gap-free, non-overlapping list of text and
 * group tokens. Malformed `{...}` (empty, unbalanced) degrade to plain text
 * rather than being chip-ified — the expander does the same (falls back to
 * literal text on a bad template).
 */
export function parseChoiceGroupTokens(text: string): PromptToken[] {
	const tokens: PromptToken[] = [];
	let cursor = 0;
	let i = 0;

	const pushText = (start: number, end: number) => {
		if (end > start) tokens.push({ type: 'text', raw: text.slice(start, end), start, end });
	};

	while (i < text.length) {
		if (text[i] === '{') {
			if (i > 0 && text[i - 1] === '$') {
				// `${name}` is a VARIABLE USAGE (see promptVariables.ts), not a choice
				// group — a bare `{name}` (one option, no `|`) is otherwise valid
				// dynamicprompts syntax, so without this guard "${mood}" would
				// parse as "$" (literal) + a single-option group chip for "mood",
				// stripping the `$` out of the rendered chip's text.
				i++;
				continue;
			}
			const close = findMatchingClose(text, i);
			if (close === -1) {
				// Unbalanced — the rest of the string is inert text.
				i++;
				continue;
			}
			const inner = text.slice(i + 1, close);
			const spec = parseGroupInner(inner);
			if (spec) {
				pushText(cursor, i);
				const raw = text.slice(i, close + 1);
				tokens.push({ type: 'group', raw, start: i, end: close + 1, spec });
				cursor = close + 1;
				i = cursor;
				continue;
			}
			// Malformed inner — treat the brace as plain text and keep scanning
			// from just past it (so a nested valid group inside can still parse).
			i++;
			continue;
		}
		i++;
	}

	pushText(cursor, text.length);
	return tokens;
}

/** Count balanced, parseable `{...}` groups in `text`. Used to notice when typing just closed one. */
export function countChoiceGroups(text: string): number {
	return parseChoiceGroupTokens(text).filter((t) => t.type === 'group').length;
}

/** Splice a group token's replacement raw text back into the full string. Pure. */
export function spliceGroupText(text: string, token: Pick<GroupToken, 'start' | 'end'>, newRaw: string): string {
	return text.slice(0, token.start) + newRaw + text.slice(token.end);
}

/** Build the raw `{...}` text for a fresh, empty two-option group — used when inserting a new one. */
export function emptyGroupRaw(): string {
	return serializeGroup({ options: [{ text: '', weight: null }, { text: '', weight: null }], count: null, countMax: null, separator: null });
}
