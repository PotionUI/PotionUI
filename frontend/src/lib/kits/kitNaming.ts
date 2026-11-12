import type { KitItemKind } from './types';

export const KIT_NAME_MAX = 32;

export type NameProblem = 'empty' | 'start' | 'chars' | 'length' | 'taken';

export interface NameCheck {
	ok: boolean;
	problem: NameProblem | null;
	message: string | null;
	suggestion: string | null;
}

const MESSAGES: Record<NameProblem, string> = {
	empty: 'Give it a name.',
	start: 'Start with a letter.',
	chars: 'Use lowercase letters, digits and _.',
	length: `At most ${KIT_NAME_MAX} characters.`,
	taken: 'Already used.'
};

export function liveName(input: string): string {
	return input.replace(/\s+/g, '_').toLowerCase();
}

export function slugify(input: string, fallback = 'kit'): string {
	const folded = input
		.normalize('NFKD')
		.replace(/[\u0300-\u036f]/g, '')
		.toLowerCase()
		.replace(/\s+/g, '_')
		.replace(/[^a-z0-9_]+/g, '_')
		.replace(/^_+|_+$/g, '');
	let name = folded;
	if (!name || !/^[a-z]/.test(name)) name = name ? `${fallback}_${name}` : fallback;
	return name.slice(0, KIT_NAME_MAX).replace(/_+$/g, '') || fallback;
}

export function suggestFree(base: string, taken: Iterable<string>): string {
	const used = new Set(taken);
	const stem = base.replace(/\d+$/, '') || base;
	const room = stem.slice(0, KIT_NAME_MAX - 3);
	for (let n = 2; n < 1000; n++) {
		const candidate = `${room}${n}`;
		if (!used.has(candidate)) return candidate;
	}
	return room;
}

export function checkName(input: string, taken: Iterable<string> = []): NameCheck {
	const name = input;
	let problem: NameProblem | null = null;
	if (name.length === 0) problem = 'empty';
	else if (name.length > KIT_NAME_MAX) problem = 'length';
	else if (!/^[a-z]/.test(name)) problem = /^[0-9_]/.test(name) ? 'start' : 'chars';
	else if (!/^[a-z][a-z0-9_]*$/.test(name)) problem = 'chars';
	else if (new Set(taken).has(name)) problem = 'taken';
	return {
		ok: problem === null,
		problem,
		message: problem ? MESSAGES[problem] : null,
		suggestion: problem === 'taken' ? suggestFree(name, taken) : null
	};
}

export function defaultItemName(kind: KitItemKind, taken: Iterable<string>): string {
	const used = new Set(taken);
	for (let n = 1; n < 1000; n++) {
		const candidate = `${kind}${n}`;
		if (!used.has(candidate)) return candidate;
	}
	return `${kind}${used.size + 1}`;
}

export function defaultItemNames(kinds: readonly KitItemKind[], taken: Iterable<string> = []): string[] {
	const used = new Set(taken);
	return kinds.map((kind) => {
		const name = defaultItemName(kind, used);
		used.add(name);
		return name;
	});
}

export function copyName(name: string): string {
	return `${name} copy`;
}
