const WORD_CHAR = '[\\p{L}\\p{N}_]';

export function normalizeForMatch(text: string): string {
	return text.normalize('NFKC').toLowerCase();
}

export function parseBannedWords(text: string): string[] {
	const seen = new Set<string>();
	const words: string[] = [];
	for (const line of text.split(/\r?\n/)) {
		const word = normalizeForMatch(line).trim().replace(/\s+/g, ' ');
		if (!word || seen.has(word)) continue;
		seen.add(word);
		words.push(word);
	}
	return words;
}

export function formatBannedWords(words: unknown): string {
	if (!Array.isArray(words)) return '';
	return words.filter((w): w is string => typeof w === 'string').join('\n');
}

function escapeRegex(value: string): string {
	return value.replace(/[.+?^${}()|[\]\\]/g, '\\$&');
}

export function compileBannedWord(word: string): RegExp {
	const body = word
		.split('*')
		.map((part) => escapeRegex(part))
		.join(`${WORD_CHAR}*`);
	return new RegExp(`(?<!${WORD_CHAR})${body}(?!${WORD_CHAR})`, 'u');
}

export function findBannedWord(prompt: string, words: string[]): string | null {
	const haystack = normalizeForMatch(prompt);
	for (const word of words) {
		if (!word.replace(/\*/g, '')) continue;
		if (compileBannedWord(word).test(haystack)) return word;
	}
	return null;
}
