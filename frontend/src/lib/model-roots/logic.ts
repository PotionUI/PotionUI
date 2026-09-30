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

export interface BindingGroup {
	model_type: string;
	items: ModelRootBinding[];
}

export function groupBindingsByType(bindings: readonly ModelRootBinding[]): BindingGroup[] {
	const groups: BindingGroup[] = [];
	for (const binding of bindings) {
		const group = groups.find((g) => g.model_type === binding.model_type);
		if (group) group.items.push(binding);
		else groups.push({ model_type: binding.model_type, items: [binding] });
	}
	return groups;
}

export function bindingTitle(binding: Pick<ModelRootBinding, 'subdir' | 'folder'>): string {
	const last = binding.subdir.split(/[\\/]/).filter(Boolean).pop();
	return last || binding.folder;
}

export function bindingRefKey(rootId: string, binding: Pick<ModelRootBinding, 'model_type' | 'subdir'>): string {
	return `${rootId}:${binding.model_type}:${binding.subdir}`;
}

function normalizedSubdir(subdir: string, caseInsensitive: boolean): string {
	const trimmed = subdir.replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
	return caseInsensitive ? trimmed.toLowerCase() : trimmed;
}

export function missingSuggestions(
	root: Pick<ModelRoot, 'bindings' | 'case_insensitive'>,
	suggestions: readonly ModelRootDetectionSuggestion[]
): ModelRootDetectionSuggestion[] {
	const have = new Set(
		root.bindings.map((b) => `${b.model_type}:${normalizedSubdir(b.subdir, root.case_insensitive)}`)
	);
	return suggestions.filter((s) => !have.has(`${s.model_type}:${normalizedSubdir(s.subdir, root.case_insensitive)}`));
}

export function profileBadgeLabel(profileId: string | null | undefined, catalog: readonly ModelLayoutSummary[]): string {
	if (!profileId) return '';
	if (profileId === GENERIC_PROFILE_ID) return 'Generic';
	return catalog.find((layout) => layout.id === profileId)?.label ?? profileId;
}

export function detectAgainApplies(root: Pick<ModelRoot, 'path'>, detection: Pick<ModelRootDetection, 'root_path' | 'path'>): boolean {
	const detected = (detection.root_path ?? detection.path).replace(/[\\/]+$/, '');
	return detected === root.path.replace(/[\\/]+$/, '');
}

export interface FolderRef {
	model_type: string;
	subdir: string;
}

export type SubdirCheck = { ok: true; subdir: string } | { ok: false; error: string };

export function normalizeSubdirInput(input: string): SubdirCheck {
	const raw = input.trim().replace(/\\/g, '/');
	if (raw === '' || raw === '.' || raw === './') return { ok: true, subdir: '' };
	if (raw.startsWith('/') || /^[A-Za-z]:/.test(raw)) {
		return { ok: false, error: 'Use a path relative to the folder, not a full path.' };
	}
	if (/[\u0000-\u001f]/.test(raw)) return { ok: false, error: 'That path has characters a folder name cannot have.' };
	const parts = raw.split('/').filter((part) => part !== '' && part !== '.');
	if (parts.includes('..')) return { ok: false, error: 'A path cannot go up with "..".' };
	return { ok: true, subdir: parts.join('/') };
}

function folderKey(subdir: string, caseInsensitive: boolean): string {
	const key = subdir.replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
	return caseInsensitive ? key.toLowerCase() : key;
}

function foldersOverlap(a: string, b: string): boolean {
	return a === '' || b === '' || a === b || a.startsWith(`${b}/`) || b.startsWith(`${a}/`);
}

export function subdirLabel(subdir: string): string {
	return subdir === '' ? 'the folder itself' : subdir;
}

export function checkFolderAgainstExisting(
	subdir: string,
	modelType: string,
	existing: readonly FolderRef[],
	caseInsensitive: boolean
): string | null {
	const key = folderKey(subdir, caseInsensitive);
	for (const other of existing) {
		const otherKey = folderKey(other.subdir, caseInsensitive);
		if (other.model_type === modelType && otherKey === key) return 'That folder is already added for this type.';
		if (foldersOverlap(key, otherKey)) {
			return `It overlaps ${subdirLabel(other.subdir)}, which is already used. A folder cannot sit inside another added folder.`;
		}
	}
	return null;
}

export function validateManualFolder(
	input: string,
	modelType: string,
	existing: readonly FolderRef[],
	caseInsensitive: boolean
): SubdirCheck {
	if (!modelType) return { ok: false, error: 'Choose a type first.' };
	const normalized = normalizeSubdirInput(input);
	if (!normalized.ok) return normalized;
	const conflict = checkFolderAgainstExisting(normalized.subdir, modelType, existing, caseInsensitive);
	return conflict ? { ok: false, error: conflict } : normalized;
}

export function manualSuggestion(modelType: string, subdir: string): ModelRootDetectionSuggestion {
	return {
		model_type: modelType,
		subdir,
		matched_by: 'manual',
		file_count: 0,
		file_count_truncated: false,
		label: subdir.split('/').filter(Boolean).pop() ?? '',
		write: false,
		source: 'manual'
	};
}

export function isManualSuggestion(suggestion: Pick<ModelRootDetectionSuggestion, 'source'>): boolean {
	return suggestion.source === 'manual';
}

export function withManualSuggestions(
	base: readonly ModelRootDetectionSuggestion[],
	manual: readonly ModelRootDetectionSuggestion[]
): ModelRootDetectionSuggestion[] {
	const seen = new Set(base.map(suggestionKey));
	return [...base, ...manual.filter((m) => !seen.has(suggestionKey(m)))];
}

export function mergeTicks(
	previous: Record<string, boolean>,
	suggestions: readonly ModelRootDetectionSuggestion[]
): Record<string, boolean> {
	const ticks: Record<string, boolean> = {};
	for (const suggestion of suggestions) {
		const key = suggestionKey(suggestion);
		ticks[key] = key in previous ? previous[key] : true;
	}
	return ticks;
}

export interface Crumb {
	label: string;
	sub: string;
}

export function breadcrumbs(sub: string): Crumb[] {
	const crumbs: Crumb[] = [{ label: 'Folder', sub: '' }];
	let acc = '';
	for (const part of sub.split('/').filter(Boolean)) {
		acc = acc ? `${acc}/${part}` : part;
		crumbs.push({ label: part, sub: acc });
	}
	return crumbs;
}
