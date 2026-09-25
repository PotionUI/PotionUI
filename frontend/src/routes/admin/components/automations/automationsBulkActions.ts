import type { Automation } from '$lib/types/automations';

export function idsNeedingEnable(automations: readonly Automation[], selected: ReadonlySet<string>): string[] {
	return automations.filter((a) => selected.has(a.id) && !a.enabled).map((a) => a.id);
}

export function idsNeedingDisable(automations: readonly Automation[], selected: ReadonlySet<string>): string[] {
	return automations.filter((a) => selected.has(a.id) && a.enabled).map((a) => a.id);
}
