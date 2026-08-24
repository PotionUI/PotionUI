// Small pure helpers used by InlineChipEditor.svelte, extracted unchanged:
// a chip-map fingerprint pair and the $-picker preview string.
import type { ChipData } from '$lib/types/segments';
import { normalizeVariableDef, type VariablesMap, type VariableRoll } from '$lib/utils/variableDefs';

/** Short preview shown next to a variable name in the `$` picker — the raw
 *  value for a text variable, or its option list for a choice variable
 *  (never raw `{a|b|c}` notation, even here). */
export function variablePreview(name: string, variables: VariablesMap): string {
	const def = normalizeVariableDef(variables[name]);
	if (def.type === 'text') return def.value;
	const options = def.options.map((o) => o.trim()).filter(Boolean);
	if (def.pinnedIndex !== null && def.options[def.pinnedIndex]?.trim()) {
		return `pinned: ${def.options[def.pinnedIndex]}`;
	}
	return options.length > 0 ? options.join(' | ') : '(no options yet)';
}

export function getChipValueMap(chipsObj: Record<string, ChipData>): Record<string, string> {
	const result: Record<string, string> = {};
	for (const [id, chip] of Object.entries(chipsObj)) {
		result[id] = chip.valueId;
	}
	return result;
}

export function getChipsHash(chipsObj: Record<string, ChipData>): string {
	return Object.entries(chipsObj)
		.map(([id, chip]) => `${id}:${chip.valueId}`)
		.sort()
		.join('|');
}

export function hashVariableRolls(rolls: Record<string, VariableRoll>): string {
	return Object.entries(rolls)
		.map(([name, r]) => `${name}:${r.optionIndex}:${r.rolledAt}`)
		.sort()
		.join('|');
}
