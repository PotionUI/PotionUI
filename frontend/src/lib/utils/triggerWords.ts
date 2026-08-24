/** Split an admin-entered trigger-word list while preserving first-seen order. */
export function parseTriggerWords(input: string): string[] {
	const seen = new Set<string>();
	const words: string[] = [];

	for (const raw of input.split(/[,\r\n]+/)) {
		const word = raw.trim();
		if (!word || seen.has(word)) continue;
		seen.add(word);
		words.push(word);
	}

	return words;
}

/** Add a pasted/typed list without duplicating values that are already present. */
export function mergeTriggerWords(existing: string[], input: string): string[] {
	const next = [...existing];
	const seen = new Set(existing);

	for (const word of parseTriggerWords(input)) {
		if (seen.has(word)) continue;
		seen.add(word);
		next.push(word);
	}

	return next;
}

function escapeRegExp(value: string): string {
	return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export interface TriggerWordMatch {
	start: number;
	end: number;
	trigger: string;
}

/**
 * Case-insensitive substring matches of any of `triggers` within `text` — the
 * single source of truth for "does this trigger word appear in this prompt
 * text", shared by the LoRA picker's "already in prompt" chip state
 * (`LoraPickerField.svelte`) and the segment editor's inline highlight
 * (`InlineChipEditor.svelte`), so the two always agree.
 *
 * Matching is plain substring containment (no word boundaries), matching the
 * picker's pre-existing `text.includes(trigger)` semantics. Longer triggers
 * are matched before shorter ones so a multi-word trigger wins over a
 * single-word trigger it contains, and overlapping matches keep only the
 * earliest/longest one at each position.
 */
export function findTriggerWordMatches(text: string, triggers: string[]): TriggerWordMatch[] {
	const uniqueTriggers = [...new Set(triggers.map((t) => t.trim()).filter(Boolean))];
	if (!text || uniqueTriggers.length === 0) return [];

	const sorted = [...uniqueTriggers].sort((a, b) => b.length - a.length);
	const pattern = new RegExp(sorted.map(escapeRegExp).join('|'), 'gi');

	const matches: TriggerWordMatch[] = [];
	let lastEnd = -1;
	let match: RegExpExecArray | null;
	while ((match = pattern.exec(text)) !== null) {
		const start = match.index;
		const end = start + match[0].length;
		if (start < lastEnd) continue;
		matches.push({ start, end, trigger: match[0] });
		lastEnd = end;
	}

	return matches;
}

/** Whether any of `triggers` appears in `text` — same semantics as
 * {@link findTriggerWordMatches}, without collecting positions. */
export function hasTriggerWordMatch(text: string, trigger: string): boolean {
	return findTriggerWordMatches(text, [trigger]).length > 0;
}
