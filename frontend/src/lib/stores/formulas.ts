import { writable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import type { Formula, FormulaDeclaration } from '$lib/formulas/types';

export interface FormulaListState {
	items: Formula[];
	loading: boolean;
	loaded: boolean;
	error: string | null;
}

const EMPTY: FormulaListState = { items: [], loading: false, loaded: false, error: null };

const lists = writable<Record<string, FormulaListState>>({});

export const formulaLists = { subscribe: lists.subscribe };

export function formulasKey(presetId: string, mode: string): string {
	return `${presetId} ${mode}`;
}

function patch(key: string, next: Partial<FormulaListState>) {
	lists.update((all) => ({ ...all, [key]: { ...(all[key] ?? EMPTY), ...next } }));
}

export async function loadFormulas(presetId: string, mode: string): Promise<void> {
	const key = formulasKey(presetId, mode);
	patch(key, { loading: true, error: null });
	try {
		const response = await api.listFormulas(presetId, mode);
		if (!response.success) throw new Error(response.error || 'Could not load formulas.');
		patch(key, { items: response.data ?? [], loading: false, loaded: true });
	} catch (error) {
		patch(key, { loading: false, error: error instanceof Error ? error.message : 'Could not load formulas.' });
	}
}

export function upsertFormula(formula: Formula): void {
	const key = formulasKey(formula.preset_id, formula.mode);
	lists.update((all) => {
		const state = all[key] ?? EMPTY;
		const exists = state.items.some((item) => item.id === formula.id);
		const items = exists
			? state.items.map((item) => (item.id === formula.id ? formula : item))
			: [formula, ...state.items];
		return { ...all, [key]: { ...state, items } };
	});
}

export function removeFormula(presetId: string, mode: string, formulaId: string): void {
	const key = formulasKey(presetId, mode);
	lists.update((all) => {
		const state = all[key] ?? EMPTY;
		return { ...all, [key]: { ...state, items: state.items.filter((item) => item.id !== formulaId) } };
	});
}

const declarations = new Map<string, Promise<FormulaDeclaration | null>>();

export function getFormulaDeclaration(
	presetId: string,
	mode: string,
	variant?: string | null,
	force = false
): Promise<FormulaDeclaration | null> {
	const key = `${presetId} ${mode} ${variant ?? ''}`;
	if (force) declarations.delete(key);
	const cached = declarations.get(key);
	if (cached) return cached;
	const request = api.getFormulaDeclaration(presetId, mode, variant ?? undefined).catch(() => {
		declarations.delete(key);
		return null;
	});
	declarations.set(key, request);
	return request;
}

export function clearFormulaDeclarations(): void {
	declarations.clear();
}
