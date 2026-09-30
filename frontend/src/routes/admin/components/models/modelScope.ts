import type { CloudModelScope, CloudModelScopePreset } from '$lib/services/admin-api';

export interface ScopeRow {
	id: string;
	title: string;
	missing: boolean;
	compatible: boolean;
}

export function scopeDraftIsDirty(draft: readonly string[], snapshot: readonly string[]): boolean {
	if (draft.length !== snapshot.length) return true;
	const known = new Set(snapshot);
	return draft.some((id) => !known.has(id));
}

export function addScopePreset(draft: readonly string[], id: string): string[] {
	return draft.includes(id) ? [...draft] : [...draft, id];
}

export function removeScopePreset(draft: readonly string[], id: string): string[] {
	return draft.filter((entry) => entry !== id);
}

export function scopeRows(draft: readonly string[], scope: Pick<CloudModelScope, 'presets' | 'candidates'>): ScopeRow[] {
	const known = new Map<string, CloudModelScopePreset>(scope.presets.map((preset) => [preset.id, preset]));
	const titles = new Map(scope.candidates.map((candidate) => [candidate.id, candidate.title]));
	return draft.map((id) => {
		const preset = known.get(id);
		if (preset?.missing) return { id, title: id, missing: true, compatible: false };
		return {
			id,
			title: preset?.title ?? titles.get(id) ?? id,
			missing: false,
			compatible: preset ? preset.compatible : titles.has(id)
		};
	});
}

export function scopeOptions(scope: Pick<CloudModelScope, 'candidates'>, draft: readonly string[]) {
	const chosen = new Set(draft);
	return scope.candidates.filter((candidate) => !chosen.has(candidate.id)).map((candidate) => ({ value: candidate.id, label: candidate.title }));
}

export function scopePayload(draft: readonly string[], scope: Pick<CloudModelScope, 'presets'>): string[] {
	const missing = new Set(scope.presets.filter((preset) => preset.missing).map((preset) => preset.id));
	return draft.filter((id) => !missing.has(id));
}
