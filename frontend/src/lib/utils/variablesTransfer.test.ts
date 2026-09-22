import { describe, it, expect } from 'vitest';
import { serializeVariables, parseVariablesImport, mergeVariables, selectReferencedVariables, summarizeVariables } from './variablesTransfer';
import type { VariablesMap } from './variableDefs';

describe('serializeVariables / parseVariablesImport round-trip', () => {
	it('round-trips a text and a conditioned choice variable', () => {
		const variables: VariablesMap = {
			music: { type: 'choice', mode: 'shuffle', pinnedIndex: null, options: ['hip hop', 'classical'] },
			dance: {
				type: 'choice',
				mode: 'shuffle',
				pinnedIndex: null,
				options: [{ text: 'breaking', when: { var: 'music', values: ['hip hop'] } }, 'freestyle']
			},
			mood: { type: 'text', value: 'moody' }
		};

		const json = serializeVariables(variables);
		const result = parseVariablesImport(json);

		expect(result.errors).toEqual([]);
		expect(result.summary).toEqual({ variables: 3, conditions: 1 });
		expect(result.variables).toEqual(variables);
	});

	it('drops blank option rows and adjusts a pinned index that pointed past them', () => {
		const variables: VariablesMap = {
			color: { type: 'choice', mode: 'pin', pinnedIndex: 2, options: ['', 'red', '', 'blue'] }
		};

		const json = serializeVariables(variables);
		const result = parseVariablesImport(json);

		expect(result.errors).toEqual([]);
		const def = result.variables.color as { options: unknown[]; pinnedIndex: number | null };
		expect(def.options).toEqual(['red', 'blue']);
		expect(def.pinnedIndex).toBe(1);
	});
});

describe('parseVariablesImport error handling', () => {
	it('rejects invalid JSON', () => {
		const result = parseVariablesImport('{not json');
		expect(result.variables).toEqual({});
		expect(result.errors).toEqual(['Invalid JSON.']);
	});

	it('rejects a non-object payload', () => {
		const result = parseVariablesImport('[1, 2, 3]');
		expect(result.variables).toEqual({});
		expect(result.errors).toEqual(['Expected an object mapping variable names to definitions.']);
	});

	it('rejects a bad variable name', () => {
		const result = parseVariablesImport(JSON.stringify({ '2bad': { type: 'text', value: 'x' } }));
		expect(result.variables).toEqual({});
		expect(result.errors).toEqual([
			"'2bad' is not a valid variable name. Use letters, digits, and underscores, starting with a letter or underscore, up to 60 characters."
		]);
	});

	it('rejects an unrecognized definition shape', () => {
		const result = parseVariablesImport(JSON.stringify({ mood: { type: 'number', value: 5 } }));
		expect(result.variables).toEqual({});
		expect(result.errors).toEqual(["'mood': unrecognized variable definition."]);
	});

	it('accepts a condition referencing a choice variable already on the target tab', () => {
		const existing: VariablesMap = {
			music: { type: 'choice', mode: 'shuffle', pinnedIndex: null, options: ['hip hop', 'classical'] }
		};
		const pasted = {
			dance: {
				type: 'choice',
				mode: 'shuffle',
				pinnedIndex: null,
				options: [{ text: 'breaking', when: { var: 'music', values: ['hip hop'] } }, 'waltz']
			}
		};

		const result = parseVariablesImport(JSON.stringify(pasted), existing);

		expect(result.errors).toEqual([]);
		expect(result.summary).toEqual({ variables: 1, conditions: 1 });
	});

	it('rejects a condition whose referenced variable is missing', () => {
		const pasted = {
			dance: {
				type: 'choice',
				mode: 'shuffle',
				pinnedIndex: null,
				options: [{ text: 'breaking', when: { var: 'musc', values: ['hip hop'] } }]
			}
		};

		const result = parseVariablesImport(JSON.stringify(pasted));

		expect(result.variables).toEqual({});
		expect(result.errors).toEqual([
			"'dance': when.var 'musc' is not a choice variable; choice variables: none defined yet."
		]);
	});

	it('rejects a cyclic condition', () => {
		const pasted = {
			a: {
				type: 'choice',
				mode: 'shuffle',
				pinnedIndex: null,
				options: [{ text: 'a1', when: { var: 'b', values: ['b1'] } }, 'a2']
			},
			b: {
				type: 'choice',
				mode: 'shuffle',
				pinnedIndex: null,
				options: [{ text: 'b1', when: { var: 'a', values: ['a1'] } }, 'b2']
			}
		};

		const result = parseVariablesImport(JSON.stringify(pasted));

		expect(result.variables.a).toBeUndefined();
		expect(result.errors.some((e) => e.includes('would make a cycle'))).toBe(true);
	});

	it('rejects a condition value that is not an exact option text', () => {
		const pasted = {
			music: { type: 'choice', mode: 'shuffle', pinnedIndex: null, options: ['hip hop', 'classical'] },
			dance: {
				type: 'choice',
				mode: 'shuffle',
				pinnedIndex: null,
				options: [{ text: 'breaking', when: { var: 'music', values: ['techno'] } }]
			}
		};

		const result = parseVariablesImport(JSON.stringify(pasted));

		expect(result.variables.dance).toBeUndefined();
		expect(result.variables.music).toBeDefined();
		expect(result.errors.some((e) => e.includes('are not options of $music'))).toBe(true);
	});
});

describe('mergeVariables', () => {
	const existing: VariablesMap = {
		mood: { type: 'text', value: 'existing' },
		keepme: { type: 'text', value: 'only here' }
	};
	const imported: VariablesMap = {
		mood: { type: 'text', value: 'imported' },
		fresh: { type: 'text', value: 'new' }
	};

	it('keep leaves clashing names untouched', () => {
		const merged = mergeVariables(existing, imported, 'keep');
		expect(merged.mood).toEqual({ type: 'text', value: 'existing' });
		expect(merged.fresh).toEqual({ type: 'text', value: 'new' });
		expect(merged.keepme).toEqual({ type: 'text', value: 'only here' });
	});

	it('replace overwrites clashing names', () => {
		const merged = mergeVariables(existing, imported, 'replace');
		expect(merged.mood).toEqual({ type: 'text', value: 'imported' });
		expect(merged.fresh).toEqual({ type: 'text', value: 'new' });
		expect(merged.keepme).toEqual({ type: 'text', value: 'only here' });
	});
});

describe('selectReferencedVariables', () => {
	const variables: VariablesMap = {
		music: { type: 'choice', mode: 'shuffle', pinnedIndex: null, options: ['hip hop', 'classical'] },
		dance: {
			type: 'choice',
			mode: 'shuffle',
			pinnedIndex: null,
			options: [{ text: 'breaking', when: { var: 'music', values: ['hip hop'] } }, 'freestyle']
		},
		mood: { type: 'text', value: 'moody' },
		unused: { type: 'text', value: 'never referenced' }
	};

	it('includes only variables the text references, dropping unused ones', () => {
		const result = selectReferencedVariables('a ${mood} scene', variables);
		expect(Object.keys(result)).toEqual(['mood']);
	});

	it('pulls in a transitive `when` dependency alongside the variable that references it', () => {
		const result = selectReferencedVariables('${dance} moves', variables);
		expect(Object.keys(result).sort()).toEqual(['dance', 'music']);
	});

	it('ignores `${name}` usages for names not present in the map', () => {
		const result = selectReferencedVariables('${ghost} and ${mood}', variables);
		expect(Object.keys(result)).toEqual(['mood']);
	});

	it('returns an empty map when the text references nothing', () => {
		const result = selectReferencedVariables('no variables here', variables);
		expect(result).toEqual({});
	});
});

describe('summarizeVariables', () => {
	it('counts variables and conditioned options', () => {
		const variables: VariablesMap = {
			music: { type: 'choice', mode: 'shuffle', pinnedIndex: null, options: ['hip hop', 'classical'] },
			dance: {
				type: 'choice',
				mode: 'shuffle',
				pinnedIndex: null,
				options: [{ text: 'breaking', when: { var: 'music', values: ['hip hop'] } }, 'freestyle']
			},
			mood: { type: 'text', value: 'moody' }
		};
		expect(summarizeVariables(variables)).toEqual({ variables: 3, conditions: 1 });
	});

	it('is empty for an empty map', () => {
		expect(summarizeVariables({})).toEqual({ variables: 0, conditions: 0 });
	});
});
