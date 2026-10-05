import {
	buildVariableWireValue,
	buildVariablesForSubmit,
	normalizeVariableDef,
	optionText,
	optionWhen,
	resolveVariableOrder,
	type ChoiceVariableDef,
	type VariablesMap
} from './variableDefs';

export interface VariableNote {
	name: string;
	phrase: string;
}

export interface ResolvedVariables {
	replacements: Record<string, string>;
	notes: VariableNote[];
}

export const SHUFFLE_PHRASE = 'shuffles each generation';
export const PER_IMAGE_PHRASE = 're-rolls per image';

const VARIABLE_REF = /\$\{([^}\s]+)\}/g;

export function resolvePreviewVariables(variables: VariablesMap | undefined): ResolvedVariables {
	const replacements: Record<string, string> = {};
	const notes: VariableNote[] = [];
	if (!variables) return { replacements, notes };

	const { wireMap } = buildVariablesForSubmit(variables, { random: () => 0 });
	const settled: Record<string, string> = {};

	for (const name of resolveVariableOrder(variables)) {
		const stored = variables[name];
		if (stored === undefined) continue;
		const def = normalizeVariableDef(stored);

		if (def.type === 'text') {
			if (wireMap[name] !== undefined) {
				replacements[name] = wireMap[name];
				settled[name] = def.value.trim();
			}
			continue;
		}

		if (def.mode === 'shuffle') {
			const eligible = def.options.filter((option) => {
				if (optionText(option).trim().length === 0) return false;
				const when = optionWhen(option);
				if (!when || settled[when.var] === undefined) return true;
				return when.values.includes(settled[when.var]);
			});
			const value = buildVariableWireValue({ ...def, options: eligible, mode: 'per-image', pinnedIndex: null } as ChoiceVariableDef);
			if (!value) continue;
			replacements[name] = value;
			if (eligible.filter((o) => optionText(o).trim()).length > 1) notes.push({ name, phrase: SHUFFLE_PHRASE });
			else settled[name] = value;
			continue;
		}

		const value = wireMap[name];
		if (value === undefined) continue;
		replacements[name] = value;
		if (def.mode === 'pin') settled[name] = value;
		else if (value.startsWith('{')) notes.push({ name, phrase: PER_IMAGE_PHRASE });
		else settled[name] = value;
	}

	return { replacements, notes };
}

export function substituteVariables(text: string, replacements: Record<string, string>): string {
	if (!text) return text;
	return text.replace(VARIABLE_REF, (match, name: string) =>
		Object.prototype.hasOwnProperty.call(replacements, name) ? replacements[name] : match
	);
}
