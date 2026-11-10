import type { PresetFixedPrompt } from '$lib/types/api';
import { resolveRequestContext } from '$lib/generation/requestContext';

export const FIXED_PROMPT_MAX = 2000;

export type FixedPromptField = 'prefix' | 'suffix' | 'negative' | 'modes';

export const FIXED_PROMPT_FIELDS: readonly FixedPromptField[] = ['prefix', 'suffix', 'negative', 'modes'];

export const EMPTY_FIXED_PROMPT: PresetFixedPrompt = { prefix: '', suffix: '', negative: '', modes: null };

export interface FixedPromptDraft {
	prefix: string;
	suffix: string;
	negative: string;
	limitModes: boolean;
	modes: string[];
}

export interface FixedPromptParts {
	prefix: string;
	suffix: string;
	negative: string;
}

export interface FixedPromptPiece {
	text: string;
	fixed: boolean;
}

export function normalizeFixedPrompt(raw: Partial<PresetFixedPrompt> | null | undefined): PresetFixedPrompt {
	return {
		prefix: typeof raw?.prefix === 'string' ? raw.prefix : '',
		suffix: typeof raw?.suffix === 'string' ? raw.suffix : '',
		negative: typeof raw?.negative === 'string' ? raw.negative : '',
		modes: Array.isArray(raw?.modes) ? raw.modes.filter((mode): mode is string => typeof mode === 'string') : null
	};
}

export function fixedPromptDraftFrom(saved: PresetFixedPrompt): FixedPromptDraft {
	return {
		prefix: saved.prefix,
		suffix: saved.suffix,
		negative: saved.negative,
		limitModes: saved.modes !== null,
		modes: saved.modes ? [...saved.modes] : []
	};
}

export function buildFixedPromptPayload(draft: FixedPromptDraft, modeOrder: readonly string[] = []): PresetFixedPrompt {
	const chosen = new Set(draft.modes);
	const ordered = [...modeOrder.filter((mode) => chosen.has(mode)), ...draft.modes.filter((mode) => !modeOrder.includes(mode))];
	return {
		prefix: draft.prefix,
		suffix: draft.suffix,
		negative: draft.negative,
		modes: draft.limitModes ? ordered : null
	};
}

function sameModes(a: string[] | null, b: string[] | null): boolean {
	if (a === null || b === null) return a === b;
	return [...a].sort().join('\u0000') === [...b].sort().join('\u0000');
}

export function fixedPromptDirtyFields(draft: FixedPromptDraft, saved: PresetFixedPrompt): FixedPromptField[] {
	const next = buildFixedPromptPayload(draft);
	const fields: FixedPromptField[] = [];
	if (next.prefix !== saved.prefix) fields.push('prefix');
	if (next.suffix !== saved.suffix) fields.push('suffix');
	if (next.negative !== saved.negative) fields.push('negative');
	if (!sameModes(next.modes, saved.modes)) fields.push('modes');
	return fields;
}

export function validateFixedPromptDraft(draft: FixedPromptDraft): Partial<Record<FixedPromptField, string>> {
	const errors: Partial<Record<FixedPromptField, string>> = {};
	for (const field of ['prefix', 'suffix', 'negative'] as const) {
		if (draft[field].length > FIXED_PROMPT_MAX) errors[field] = `Keep this to ${FIXED_PROMPT_MAX} characters.`;
	}
	if (draft.limitModes && draft.modes.length === 0) errors.modes = 'Pick at least one mode, or apply it to every mode.';
	return errors;
}

export function isFixedPromptEmpty(fixed: Pick<PresetFixedPrompt, 'prefix' | 'suffix' | 'negative'>): boolean {
	return !fixed.prefix.trim() && !fixed.suffix.trim() && !fixed.negative.trim();
}

export function fixedPromptForMode(
	fixed: PresetFixedPrompt | null | undefined,
	mode: string | null | undefined,
	options: { directorActive?: boolean } = {}
): FixedPromptParts | null {
	if (!fixed || isFixedPromptEmpty(fixed)) return null;
	if (options.directorActive) return null;
	if (fixed.modes !== null && (!mode || !fixed.modes.includes(mode))) return null;
	return { prefix: fixed.prefix.trim(), suffix: fixed.suffix.trim(), negative: fixed.negative.trim() };
}

export function fixedPromptPieces(
	userText: string,
	fixed: FixedPromptParts | null | undefined,
	channel: 'prompt' | 'negative'
): FixedPromptPiece[] {
	const user = userText ?? '';
	const parts: FixedPromptPiece[] =
		channel === 'prompt'
			? [
					{ text: fixed?.prefix ?? '', fixed: true },
					{ text: user, fixed: false },
					{ text: fixed?.suffix ?? '', fixed: true }
				]
			: [
					{ text: user, fixed: false },
					{ text: fixed?.negative ?? '', fixed: true }
				];
	const present = parts.filter((part) => part.text.trim() !== '');
	const separator = channel === 'prompt' ? ' ' : ', ';
	const pieces: FixedPromptPiece[] = [];
	present.forEach((part, index) => {
		if (index > 0) pieces.push({ text: separator, fixed: part.fixed || present[index - 1].fixed });
		pieces.push(part);
	});
	return pieces;
}

export function directorModeNames(
	vars: Record<string, unknown> | null | undefined,
	modes: readonly string[]
): string[] {
	if (!vars) return [];
	return modes.filter((mode) => {
		const context = resolveRequestContext(vars, mode);
		return context.videoDirectorActive || context.musicDirectorActive;
	});
}
