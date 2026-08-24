/**
 * Pure helpers for SectionField.svelte's persisted fold state - kept outside
 * the component for the same reason as gateState.ts: no Svelte-component-
 * mounting test harness in this repo (vitest runs `environment: 'node'`).
 * See sectionCollapsedContext.ts for the Svelte context these back.
 */

/** Key a remembered fold state by preset + mode so switching either can never
 *  leak one preset's fold state onto another's identically-pathed section. */
export function buildSectionStorageKey(
	preset: string | null | undefined,
	mode: string | null | undefined,
	fieldPath: string | null | undefined
): string | null {
	if (!preset || !mode || !fieldPath) return null;
	return `${preset}/${mode}/${fieldPath}`;
}

/** Narrow the flat `preset/mode/fieldPath` store down to the entries for one
 *  preset+mode, re-keyed by bare fieldPath. Lets SectionField look itself up by
 *  path without knowing which preset or mode it is rendering under. */
export function foldedForScope(
	stored: Record<string, boolean> | null | undefined,
	preset: string | null | undefined,
	mode: string | null | undefined
): Record<string, boolean> {
	if (!stored || !preset || !mode) return {};
	const prefix = `${preset}/${mode}/`;
	const scoped: Record<string, boolean> = {};
	for (const [key, collapsed] of Object.entries(stored)) {
		if (key.startsWith(prefix)) scoped[key.slice(prefix.length)] = collapsed;
	}
	return scoped;
}

/** A remembered value always wins; the YAML `collapsed` default only applies
 *  the first time a section is seen (nothing remembered yet). */
export function resolveSectionCollapsed(config: any, remembered: boolean | undefined): boolean {
	if (remembered !== undefined) return remembered;
	return config?.collapsed === true;
}
