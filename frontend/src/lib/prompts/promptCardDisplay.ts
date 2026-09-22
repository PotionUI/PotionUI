import { normalizeVariableDef, optionWhen, whenIsBroken, type VariablesMap } from '$lib/utils/variableDefs';
import type { Prompt } from '$lib/types/segments';

export interface PromptCardTitle {
	text: string;
	untitled: boolean;
}

const UNTITLED_PREVIEW_WORDS = 6;

export function promptCardTitle(prompt: Pick<Prompt, 'name' | 'flattened_text'>): PromptCardTitle {
	if (prompt.name) return { text: prompt.name, untitled: false };
	const preview = (prompt.flattened_text || '').trim().replace(/\s+/g, ' ');
	if (!preview) return { text: 'Untitled', untitled: true };
	const words = preview.split(' ');
	const truncated = words.slice(0, UNTITLED_PREVIEW_WORDS).join(' ');
	const suffix = words.length > UNTITLED_PREVIEW_WORDS ? '…' : '';
	return { text: `Untitled · "${truncated}${suffix}"`, untitled: true };
}

export interface PromptVariablesSummary {
	count: number;
	linked: number;
	broken: boolean;
}

export function summarizePromptVariables(variables: VariablesMap | null | undefined): PromptVariablesSummary {
	if (!variables) return { count: 0, linked: 0, broken: false };
	const names = Object.keys(variables);
	let linked = 0;
	let broken = false;
	for (const name of names) {
		const def = normalizeVariableDef(variables[name]);
		if (def.type !== 'choice') continue;
		for (const option of def.options) {
			const when = optionWhen(option);
			if (!when) continue;
			linked++;
			if (whenIsBroken(when, variables)) broken = true;
		}
	}
	return { count: names.length, linked, broken };
}
