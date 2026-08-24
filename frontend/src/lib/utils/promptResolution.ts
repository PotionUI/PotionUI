// Aligns an authored prompt TEMPLATE ({a|b} choice groups, ${name} variable
// usages, plain text) against its already-EXPANDED, per-image RENDERED text
// (as reported by the `rendered_prompt` pipe artifact — see
// src/pipelines/pipes/dynamic_prompts_renderer/main.py and
// src/features/prompt/expander.py) so the artifact card can show WHAT each
// dynamic construct resolved to.
//
// This is display-only: it never changes roll semantics (each image still
// re-rolls its own `{a|b}`/`${var}` independently — expander.py samples once
// per image off `base_seed + index`). It just reconstructs, after the fact,
// which substring of the rendered text a given template token produced.
//
// Algorithm: sequentially match every STATIC text chunk from the template
// inside the rendered string (in order, never backtracking before the
// previous match). Whatever falls between two static anchors (or before the
// first / after the last) is the resolution of the dynamic token(s) that sit
// there. A run of more than one consecutive dynamic token (no static text
// between them) has no anchor to split on — that boundary is genuinely
// ambiguous, so the whole run is reported as one merged, `ambiguous: true`
// entry rather than guessing a split point.
//
// Alignment can fail (a static chunk doesn't appear in the rendered text at
// the expected position — e.g. a `prompt.transform` plugin rewrote the text,
// or the mode bypasses expansion entirely, such as Video Director / Prompt
// Relay). On failure this returns `null` so the caller can fall back to
// plain-text rendering. It must never fabricate a resolution.

import { parsePromptTokens, type PromptToken } from './promptTokens';

export type ResolutionKind = 'group' | 'variable' | 'mixed';

export interface StaticSpan {
	type: 'static';
	text: string;
}

export interface ResolvedSpan {
	type: 'resolved';
	/** The substring of the rendered text this token (or ambiguous run) produced. May be ''. */
	text: string;
	/** Human-readable label for the originating token: `{a|b}` for a group, `${name}` for a variable. */
	label: string;
	kind: ResolutionKind;
	/** True when this span merges >1 consecutive dynamic tokens with no anchor between them. */
	ambiguous: boolean;
}

export type PromptSpan = StaticSpan | ResolvedSpan;

export interface RolledEntry {
	label: string;
	kind: ResolutionKind;
	resolvedText: string;
	ambiguous: boolean;
}

export interface PromptAlignment {
	/** Ordered, gap-free spans covering the entire rendered string. */
	spans: PromptSpan[];
	/** One entry per dynamic occurrence (or ambiguous run), in template order — the "what rolled" list. */
	rolled: RolledEntry[];
}

function labelForToken(token: PromptToken): string {
	// GroupToken.raw is already the full `{...}` source text; VariableUsageToken.raw is already `${name}`.
	return (token as { raw: string }).raw;
}

/**
 * Align `template` (pre-expansion, `{a|b}`/`${var}` intact) against `rendered`
 * (the fully-expanded text for one image). Returns `null` when the two can't
 * be confidently reconciled — the caller should fall back to plain rendering.
 */
export function alignTemplateToRendered(template: string, rendered: string): PromptAlignment | null {
	// The backend strips only the OUTER edges of the final rendered result
	// (expander.py `_sample_one` -> `.strip()`), never internal whitespace.
	// Trimming the template the same way keeps a leading/trailing static
	// text token from mismatching purely because of that edge trim.
	const trimmedTemplate = template.trim();
	if (!trimmedTemplate) {
		return rendered.trim() === '' ? { spans: [], rolled: [] } : null;
	}

	const tokens = parsePromptTokens(trimmedTemplate);
	const spans: PromptSpan[] = [];
	const rolled: RolledEntry[] = [];
	let cursor = 0;
	let i = 0;

	while (i < tokens.length) {
		const token = tokens[i];

		if (token.type === 'text') {
			const idx = rendered.indexOf(token.raw, cursor);
			if (idx !== cursor) {
				// Either not found (-1) or found later than expected — either way
				// there's unexplained content between the last anchor and this one
				// that we can't safely attribute. Give up rather than guess.
				return null;
			}
			spans.push({ type: 'static', text: token.raw });
			cursor = idx + token.raw.length;
			i++;
			continue;
		}

		// A run of one or more consecutive dynamic (group/variable) tokens.
		const runStart = i;
		while (i < tokens.length && tokens[i].type !== 'text') i++;
		const run = tokens.slice(runStart, i);

		let runEnd: number;
		if (i < tokens.length) {
			const nextText = tokens[i];
			const idx = rendered.indexOf(nextText.raw, cursor);
			if (idx === -1 || idx < cursor) return null;
			runEnd = idx;
		} else {
			runEnd = rendered.length;
		}

		const resolvedText = rendered.slice(cursor, runEnd);

		if (run.length === 1) {
			const only = run[0];
			const label = labelForToken(only);
			const kind: ResolutionKind = only.type === 'group' ? 'group' : 'variable';
			spans.push({ type: 'resolved', text: resolvedText, label, kind, ambiguous: false });
			rolled.push({ label, kind, resolvedText, ambiguous: false });
		} else {
			const kinds = new Set(run.map((t) => t.type));
			const kind: ResolutionKind = kinds.size === 1 ? (run[0].type === 'group' ? 'group' : 'variable') : 'mixed';
			const label = run.map(labelForToken).join(' + ');
			spans.push({ type: 'resolved', text: resolvedText, label, kind, ambiguous: true });
			rolled.push({ label, kind, resolvedText, ambiguous: true });
		}

		cursor = runEnd;
	}

	if (cursor !== rendered.length) {
		// Trailing rendered content the template can't account for.
		return null;
	}

	return { spans, rolled };
}
