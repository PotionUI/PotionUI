export interface PromptPickerContext {
	before: string;
	after: string;
}

function tailWords(text: string, count: number): string {
	const words = text.trim().split(/\s+/).filter(Boolean);
	if (words.length === 0) return '';
	const slice = words.slice(Math.max(0, words.length - count));
	return (words.length > count ? '…' : '') + slice.join(' ');
}

function headWords(text: string, count: number): string {
	const words = text.trim().split(/\s+/).filter(Boolean);
	if (words.length === 0) return '';
	const slice = words.slice(0, count);
	return slice.join(' ') + (words.length > count ? '…' : '');
}

export function buildPromptPickerContext(
	text: string,
	start: number,
	end: number,
	wordsEach: number = 6
): PromptPickerContext {
	const safeStart = Math.max(0, Math.min(start, text.length));
	const safeEnd = Math.max(safeStart, Math.min(end, text.length));
	return {
		before: tailWords(text.slice(0, safeStart), wordsEach),
		after: headWords(text.slice(safeEnd), wordsEach)
	};
}
