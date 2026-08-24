/**
 * Pure helpers for GateField.svelte - kept outside the component because
 * there is no Svelte-component-mounting test harness in this repo (vitest
 * runs `environment: 'node'`).
 */

/** Resolve a gate's own boolean state from the ambient form-data object. */
export function resolveGateOn(config: any, value: any): boolean {
	const name = config?.name;
	const raw = name && value && typeof value === 'object' ? value[name] : undefined;
	if (raw === undefined || raw === null) {
		return config?.default === true;
	}
	return Boolean(raw);
}

/** A gate with an empty/missing `children:` degrades to a bare toggle. */
export function gateHasChildren(config: any): boolean {
	return Array.isArray(config?.children) && config.children.length > 0;
}

/** Stable id for the expandable region, used to wire `aria-controls`. */
export function gateRegionId(config: any): string {
	const name = typeof config?.name === 'string' && config.name ? config.name : 'field';
	return `gate-${name}-content`;
}
