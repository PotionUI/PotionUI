import {
	normalizeVariableDef,
	optionText,
	optionWhen,
	dependencies,
	type ChoiceVariableDef,
	type ChoiceVariableMode,
	type VariableDef,
	type VariableOption,
	type VariableOptionWhen,
	type VariablesMap
} from './variableDefs';
import { isValidVariableName, extractVariableUsages } from './promptVariables';

const MAX_NAME_CHARS = 60;

function dropBlankOptions(def: ChoiceVariableDef): { options: VariableOption[]; pinnedIndex: number | null } {
	const options: VariableOption[] = [];
	let pinnedIndex = def.pinnedIndex;
	def.options.forEach((option, index) => {
		const text = optionText(option).trim();
		if (!text) {
			if (pinnedIndex !== null) {
				if (pinnedIndex === index) pinnedIndex = null;
				else if (pinnedIndex > index) pinnedIndex -= 1;
			}
			return;
		}
		const when = optionWhen(option);
		options.push(when ? { text, when } : text);
	});
	return { options, pinnedIndex };
}

export function serializeVariables(variables: VariablesMap): string {
	const out: VariablesMap = {};
	for (const name of Object.keys(variables)) {
		const def = normalizeVariableDef(variables[name]);
		if (def.type === 'text') {
			out[name] = { type: 'text', value: def.value };
			continue;
		}
		const { options, pinnedIndex } = dropBlankOptions(def);
		out[name] = { type: 'choice', mode: def.mode, pinnedIndex, options };
	}
	return JSON.stringify(out, null, 2);
}

export interface VariablesImportSummary {
	variables: number;
	conditions: number;
}

export interface ParsedVariablesImport {
	variables: VariablesMap;
	summary: VariablesImportSummary;
	errors: string[];
}

function parseOptionShape(name: string, rawOpt: unknown, errors: string[]): VariableOption | null | undefined {
	if (typeof rawOpt === 'string') {
		const text = rawOpt.trim();
		return text ? text : null;
	}
	if (!rawOpt || typeof rawOpt !== 'object' || Array.isArray(rawOpt)) {
		errors.push(`'${name}': malformed option.`);
		return undefined;
	}
	const obj = rawOpt as Record<string, unknown>;
	if (typeof obj.text !== 'string') {
		errors.push(`'${name}': malformed option.`);
		return undefined;
	}
	const text = obj.text.trim();
	if (!text) return null;
	if (obj.when === undefined) return text;

	const rawWhen = obj.when;
	const whenValues = rawWhen && typeof rawWhen === 'object' ? (rawWhen as Record<string, unknown>).values : undefined;
	if (
		!rawWhen ||
		typeof rawWhen !== 'object' ||
		typeof (rawWhen as Record<string, unknown>).var !== 'string' ||
		!Array.isArray(whenValues) ||
		!whenValues.every((v: unknown) => typeof v === 'string')
	) {
		errors.push(`'${name}': malformed condition.`);
		return undefined;
	}
	const when = rawWhen as { var: string; values: string[] };
	return { text, when: { var: when.var, values: when.values } };
}

function parseDefShape(name: string, rawDef: unknown, errors: string[]): VariableDef | null {
	if (!rawDef || typeof rawDef !== 'object' || Array.isArray(rawDef)) {
		errors.push(`'${name}': unrecognized variable definition.`);
		return null;
	}
	const obj = rawDef as Record<string, unknown>;

	if (obj.type === 'text') {
		if (typeof obj.value !== 'string') {
			errors.push(`'${name}': text variable needs a string value.`);
			return null;
		}
		return { type: 'text', value: obj.value };
	}

	if (obj.type === 'choice') {
		if (!Array.isArray(obj.options) || obj.options.length === 0) {
			errors.push(`'${name}': a choice variable needs at least one non-empty option.`);
			return null;
		}
		const options: VariableOption[] = [];
		for (const rawOpt of obj.options) {
			const option = parseOptionShape(name, rawOpt, errors);
			if (option === undefined) return null;
			if (option !== null) options.push(option);
		}
		if (options.length === 0) {
			errors.push(`'${name}': a choice variable needs at least one non-empty option.`);
			return null;
		}
		const mode: ChoiceVariableMode = obj.mode === 'pin' || obj.mode === 'per-image' ? obj.mode : 'shuffle';
		let pinnedIndex: number | null = null;
		if (
			typeof obj.pinnedIndex === 'number' &&
			Number.isInteger(obj.pinnedIndex) &&
			obj.pinnedIndex >= 0 &&
			obj.pinnedIndex < options.length
		) {
			pinnedIndex = obj.pinnedIndex;
		}
		return { type: 'choice', options, mode, pinnedIndex };
	}

	errors.push(`'${name}': unrecognized variable definition.`);
	return null;
}

function validateCondition(
	name: string,
	when: VariableOptionWhen,
	working: VariablesMap,
	errors: string[]
): VariableOptionWhen | null {
	if (!when.values || when.values.length === 0) {
		errors.push(`'${name}': when.values must not be empty.`);
		return null;
	}
	if (when.var === name) {
		errors.push(`'${name}': when.var cannot reference '${name}' itself.`);
		return null;
	}
	const refStored = working[when.var];
	const ref = refStored !== undefined ? normalizeVariableDef(refStored) : undefined;
	if (!ref || ref.type !== 'choice') {
		const choices = Object.keys(working).filter((n) => {
			if (n === name) return false;
			const stored = working[n];
			return stored !== undefined && normalizeVariableDef(stored).type === 'choice';
		});
		const listing = choices.length > 0 ? choices.join(', ') : 'none defined yet';
		errors.push(`'${name}': when.var '${when.var}' is not a choice variable; choice variables: ${listing}.`);
		return null;
	}
	if (dependencies(when.var, working, true).includes(name)) {
		errors.push(
			`'${name}': when.var '${when.var}' would make a cycle (${when.var} already resolves after ${name}).`
		);
		return null;
	}
	const refTexts = new Set(ref.options.map((o) => optionText(o).trim()));
	const bad = when.values.filter((v) => !refTexts.has(v));
	if (bad.length > 0) {
		const badList = `[${bad.map((v) => JSON.stringify(v)).join(', ')}]`;
		errors.push(
			`'${name}': when.values ${badList} are not options of $${when.var}; its options are ${[...refTexts].join(', ')}.`
		);
		return null;
	}
	return { var: when.var, values: when.values };
}

export function parseVariablesImport(text: string, existing: VariablesMap = {}): ParsedVariablesImport {
	const errors: string[] = [];
	const empty: ParsedVariablesImport = { variables: {}, summary: { variables: 0, conditions: 0 }, errors };

	let parsed: unknown;
	try {
		parsed = JSON.parse(text);
	} catch {
		errors.push('Invalid JSON.');
		return empty;
	}

	if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
		errors.push('Expected an object mapping variable names to definitions.');
		return empty;
	}

	const raw = parsed as Record<string, unknown>;
	const draft: VariablesMap = {};

	for (const name of Object.keys(raw)) {
		if (!isValidVariableName(name) || name.length > MAX_NAME_CHARS) {
			errors.push(
				`'${name}' is not a valid variable name. Use letters, digits, and underscores, starting with a letter or underscore, up to ${MAX_NAME_CHARS} characters.`
			);
			continue;
		}
		const def = parseDefShape(name, raw[name], errors);
		if (def) draft[name] = def;
	}

	const working: VariablesMap = { ...existing, ...draft };
	const variables: VariablesMap = {};

	for (const name of Object.keys(draft)) {
		const def = draft[name] as VariableDef;
		if (def.type === 'text') {
			variables[name] = def;
			continue;
		}
		const validatedOptions: VariableOption[] = [];
		let rejected = false;
		for (const option of def.options) {
			const when = optionWhen(option);
			if (!when) {
				validatedOptions.push(option);
				continue;
			}
			const checked = validateCondition(name, when, working, errors);
			if (!checked) {
				rejected = true;
				break;
			}
			validatedOptions.push({ text: optionText(option), when: checked });
		}
		if (rejected) continue;
		variables[name] = { ...def, options: validatedOptions };
	}

	return { variables, summary: summarizeVariables(variables), errors };
}

export function summarizeVariables(variables: VariablesMap): VariablesImportSummary {
	const conditions = Object.keys(variables).reduce((sum, name) => {
		const def = normalizeVariableDef(variables[name]);
		if (def.type !== 'choice') return sum;
		return sum + def.options.filter((o) => !!optionWhen(o)).length;
	}, 0);
	return { variables: Object.keys(variables).length, conditions };
}

/** Names `text` references via `${name}`, plus their transitive `when`
 *  dependencies, restricted to names actually present in `variables` -- the
 *  subset a saved Prompt should carry so an imported `$dance` conditioned on
 *  `$music` brings `$music` along with it. */
export function selectReferencedVariables(text: string, variables: VariablesMap): VariablesMap {
	const used = extractVariableUsages(text).filter((name) => name in variables);
	const names = new Set(used);
	for (const name of used) {
		for (const dep of dependencies(name, variables, true)) {
			if (dep in variables) names.add(dep);
		}
	}
	const out: VariablesMap = {};
	for (const name of names) out[name] = variables[name];
	return out;
}

export function mergeVariables(existing: VariablesMap, imported: VariablesMap, rule: 'keep' | 'replace'): VariablesMap {
	const merged: VariablesMap = { ...existing };
	for (const name of Object.keys(imported)) {
		if (rule === 'keep' && name in merged) continue;
		merged[name] = imported[name];
	}
	return merged;
}
