/**
 * Split a proposed segment's content (the chat's "Suggested change" preview)
 * into plain text, `#phrasebook` markers and `${variable}` references — the
 * same marker grammar chipParser.ts uses — so both render as chips instead of
 * raw marker text.
 */
export type PreviewToken = { kind: 'text' | 'phrasebook' | 'variable'; text: string; label: string };

const MARKER_RE = /#\[([^\]]+)\]|#([\w][\w.]*)|\$\{([^}]+)\}/g;

export function splitMarkerTokens(text: string): PreviewToken[] {
	const tokens: PreviewToken[] = [];
	let lastIndex = 0;
	for (const match of text.matchAll(MARKER_RE)) {
		const index = match.index ?? 0;
		if (index > lastIndex) tokens.push({ kind: 'text', text: text.slice(lastIndex, index), label: '' });
		const [raw, bracketed, bare, variable] = match;
		tokens.push(
			variable !== undefined
				? { kind: 'variable', text: raw, label: variable.trim() }
				: { kind: 'phrasebook', text: raw, label: bracketed ?? bare }
		);
		lastIndex = index + raw.length;
	}
	if (lastIndex < text.length) tokens.push({ kind: 'text', text: text.slice(lastIndex), label: '' });
	return tokens;
}
