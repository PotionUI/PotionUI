import type {
	ModelLayoutSummary,
	ModelRoot,
	ModelRootBinding,
	ModelRootDetection,
	ModelRootDetectionSuggestion,
	ModelRootProfileAlternative
} from '$lib/services/api/models';

export function orderedRoots(roots: ModelRoot[]): ModelRoot[] {
	return [...roots].sort((a, b) => (a.kind === 'home' ? -1 : b.kind === 'home' ? 1 : 0));
}

export function bindingsSummary(root: ModelRoot): { files: number; bytes: number } {
	return root.bindings.reduce(
		(acc, b) => ({ files: acc.files + b.indexed_files, bytes: acc.bytes + b.size_bytes }),
		{ files: 0, bytes: 0 }
	);
}

export function mergeDetectionSuggestions(
	detection: ModelRootDetection | null
): ModelRootDetectionSuggestion[] {
	if (!detection) return [];
	if (detection.layout === 'typed') return detection.suggestions;
	if (detection.layout === 'single' && detection.single_type_guess) {
		return [
			{
				model_type: detection.single_type_guess,
				subdir: '',
				matched_by: 'canonical',
				file_count: 0,
				file_count_truncated: false
			}
		];
	}
	return [];
}

export const HEADER_SCAN_TYPES: readonly string[] = ['checkpoint', 'diffusion_model', 'unet'];

export function bindingSupportsHeaderScan(binding: Pick<ModelRootBinding, 'model_type'>): boolean {
	return HEADER_SCAN_TYPES.includes(binding.model_type);
}

export function bindingScanKey(rootId: string, binding: Pick<ModelRootBinding, 'model_type' | 'subdir'>): string {
	return `${rootId}:${binding.model_type}:${binding.subdir}`;
}

export const GENERIC_PROFILE_ID = 'generic';

export function suggestionKey(suggestion: Pick<ModelRootDetectionSuggestion, 'model_type' | 'subdir'>): string {
	return `${suggestion.model_type}:${suggestion.subdir}`;
}

export interface SuggestionGroup {
	model_type: string;
	items: ModelRootDetectionSuggestion[];
}

export function groupSuggestionsByType(suggestions: readonly ModelRootDetectionSuggestion[]): SuggestionGroup[] {
	const groups: SuggestionGroup[] = [];
	for (const suggestion of suggestions) {
		const group = groups.find((g) => g.model_type === suggestion.model_type);
		if (group) group.items.push(suggestion);
		else groups.push({ model_type: suggestion.model_type, items: [suggestion] });
	}
	return groups;
}

export function initialTicks(suggestions: readonly ModelRootDetectionSuggestion[]): Record<string, boolean> {
	const ticks: Record<string, boolean> = {};
	for (const suggestion of suggestions) ticks[suggestionKey(suggestion)] = true;
	return ticks;
}

export function initialWriteChoices(suggestions: readonly ModelRootDetectionSuggestion[]): Record<string, string> {
	const choices: Record<string, string> = {};
	for (const group of groupSuggestionsByType(suggestions)) {
		const preferred = group.items.find((s) => s.write) ?? group.items[0];
		choices[group.model_type] = preferred.subdir;
	}
	return choices;
}

export function effectiveWriteChoices(
	suggestions: readonly ModelRootDetectionSuggestion[],
	ticks: Record<string, boolean>,
	choices: Record<string, string>
): Record<string, string> {
	const resolved: Record<string, string> = {};
	for (const group of groupSuggestionsByType(suggestions)) {
		const tickedItems = group.items.filter((s) => ticks[suggestionKey(s)]);
		if (tickedItems.length === 0) continue;
		const chosen = tickedItems.find((s) => s.subdir === choices[group.model_type]) ?? tickedItems[0];
		resolved[group.model_type] = chosen.subdir;
	}
	return resolved;
}

export interface BuiltBinding {
	model_type: string;
	subdir: string;
	scan_headers?: boolean;
	write: boolean;
}

export function buildBindings(
	suggestions: readonly ModelRootDetectionSuggestion[],
	ticks: Record<string, boolean>,
	choices: Record<string, string>,
	downloadsHere: boolean
): { bindings: BuiltBinding[]; writeTypes: string[] } {
	const resolved = effectiveWriteChoices(suggestions, ticks, choices);
	const bindings = suggestions
		.filter((s) => ticks[suggestionKey(s)])
		.map((s) => ({
			model_type: s.model_type,
			subdir: s.subdir,
			...(s.scan_headers !== undefined ? { scan_headers: s.scan_headers } : {}),
			write: downloadsHere && resolved[s.model_type] === s.subdir
		}));
	return { bindings, writeTypes: downloadsHere ? Object.keys(resolved) : [] };
}

export function typesWithChoice(
	suggestions: readonly ModelRootDetectionSuggestion[],
	ticks: Record<string, boolean>
): Set<string> {
	const result = new Set<string>();
	for (const group of groupSuggestionsByType(suggestions)) {
		if (group.items.filter((s) => ticks[suggestionKey(s)]).length >= 2) result.add(group.model_type);
	}
	return result;
}

export interface LayoutOption {
	id: string;
	label: string;
}

export function layoutOptions(
	alternatives: readonly ModelRootProfileAlternative[],
	current: { id: string; label: string } | null,
	catalog: readonly ModelLayoutSummary[]
): LayoutOption[] {
	const options: LayoutOption[] = [];
	const seen = new Set<string>();
	const add = (id: string, label: string) => {
		if (seen.has(id)) return;
		seen.add(id);
		options.push({ id, label });
	};
	if (current) add(current.id, current.label);
	for (const alt of alternatives) if (alt.id !== GENERIC_PROFILE_ID) add(alt.id, alt.label);
	for (const layout of catalog) if (layout.id !== GENERIC_PROFILE_ID) add(layout.id, layout.label);
	add(GENERIC_PROFILE_ID, alternatives.find((a) => a.id === GENERIC_PROFILE_ID)?.label ?? 'Generic (folder names)');
	return options;
}

export function detectedLabel(profile: { label: string; confidence: 'strong' | 'weak' | null } | null | undefined): string {
	if (!profile) return 'No known layout detected';
	return profile.confidence === 'weak' ? `Looks like: ${profile.label}` : `Detected: ${profile.label}`;
}

export function pendingExtraPaths(
	extras: readonly { path: string }[],
	ticks: Record<string, boolean>,
	done: Record<string, boolean>
): string[] {
	return extras.filter((e) => ticks[e.path] && !done[e.path]).map((e) => e.path);
}
