import { describeEditorTaskFailure } from '$lib/services/api/editorTasks';

export interface Tweak {
	key: string;
	owned: string[];
	defaults: Record<string, unknown>;
	base: Record<string, unknown>;
	values: Record<string, unknown>;
	settled: boolean;
}

export const SAME_NAME_MESSAGE = 'You already have a setup with that name. Choose another name.';
export const NAME_REQUIRED_MESSAGE = 'Enter a name for the setup.';
export const NAME_TOO_LONG_MESSAGE = 'Use a shorter name.';
export const MAX_NAME_LENGTH = 80;

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function sameValue(a: unknown, b: unknown): boolean {
	if (a === b) return true;
	if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((item, index) => sameValue(item, b[index]));
	if (isRecord(a) && isRecord(b)) {
		const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
		for (const key of keys) if (!sameValue(a[key], b[key])) return false;
		return true;
	}
	return false;
}

export function withoutKeys(values: Record<string, unknown>, owned: ReadonlySet<string> | readonly string[]): Record<string, unknown> {
	const skip = owned instanceof Set ? owned : new Set(owned);
	return Object.fromEntries(Object.entries(values).filter(([key]) => !skip.has(key)));
}

export function sameSettings(
	a: Record<string, unknown>,
	b: Record<string, unknown>,
	defaults: Record<string, unknown>,
	owned: readonly string[]
): boolean {
	const skip = new Set(owned);
	const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
	for (const key of keys) {
		if (skip.has(key)) continue;
		const left = a[key] === undefined ? defaults[key] : a[key];
		const right = b[key] === undefined ? defaults[key] : b[key];
		if (!sameValue(left, right)) return false;
	}
	return true;
}

export function startTweak(key: string, owned: readonly string[], defaults: Record<string, unknown>, base: Record<string, unknown>): Tweak {
	return { key, owned: [...owned], defaults, base: { ...base }, values: { ...base }, settled: false };
}

export function editTweak(tweak: Tweak, data: Record<string, unknown>): Tweak {
	if (!tweak.settled) return { ...tweak, base: withoutKeys(data, tweak.owned), values: data, settled: true };
	return { ...tweak, values: data };
}

export function savedTweak(tweak: Tweak): Tweak {
	return { ...tweak, base: withoutKeys(tweak.values, tweak.owned), settled: true };
}

export function isChanged(tweak: Tweak | null | undefined): boolean {
	return !!tweak && tweak.settled && !sameSettings(tweak.values, tweak.base, tweak.defaults, tweak.owned);
}

export function runValues(base: Record<string, unknown>, tweak: Tweak | null | undefined, key: string): Record<string, unknown> {
	if (!tweak || tweak.key !== key || !isChanged(tweak)) return base;
	return withoutKeys(tweak.values, tweak.owned);
}

export interface SaveTarget {
	mode: string;
	variant: string | null;
}

export function sessionData(target: SaveTarget, values: Record<string, unknown>, owned: readonly string[]): Record<string, Record<string, unknown>> {
	const entry: Record<string, unknown> = { formData: withoutKeys(values, owned) };
	if (target.variant) entry.selectedVariant = target.variant;
	return { [target.mode]: entry };
}

export interface SaveToBody {
	name: string;
	data: Record<string, Record<string, unknown>>;
	mode: string;
	base_version: number | null;
	kind: 'editor';
}

export interface SaveNewBody {
	preset_id: string;
	name: string;
	data: Record<string, Record<string, unknown>>;
	mode: string;
	kind: 'editor';
}

export function saveToBody(input: { name: string; version: number | null; target: SaveTarget; values: Record<string, unknown>; owned: readonly string[] }): SaveToBody {
	return {
		name: input.name,
		data: sessionData(input.target, input.values, input.owned),
		mode: input.target.mode,
		base_version: input.version,
		kind: 'editor'
	};
}

export function saveAsNewBody(input: { presetId: string; name: string; target: SaveTarget; values: Record<string, unknown>; owned: readonly string[] }): SaveNewBody {
	return {
		preset_id: input.presetId,
		name: input.name,
		data: sessionData(input.target, input.values, input.owned),
		mode: input.target.mode,
		kind: 'editor'
	};
}

export function validateSetupName(raw: string, existing: readonly string[]): string | null {
	const name = raw.trim();
	if (!name) return NAME_REQUIRED_MESSAGE;
	if (name.length > MAX_NAME_LENGTH) return NAME_TOO_LONG_MESSAGE;
	const lower = name.toLowerCase();
	if (existing.some((entry) => entry.trim().toLowerCase() === lower)) return SAME_NAME_MESSAGE;
	return null;
}

export function sessionSaveMessage(error: unknown): string {
	const failure = describeEditorTaskFailure(error);
	switch (failure.kind) {
		case 'network':
			return 'The connection was lost. Try again.';
		case 'server':
			return 'Something went wrong on the server. Try again.';
		case 'not_found':
			return 'This setup no longer exists. Save it as a new setup instead.';
		case 'limit':
			return failure.message ?? 'You cannot save setups right now.';
		case 'rejected':
			switch (failure.code) {
				case 'session_kind_mismatch':
					return 'That name is already used by one of your sessions on the Generate page. Choose another name.';
				case 'session_version_conflict':
					return 'This setup was changed somewhere else. Your changes are kept. Save again to replace it.';
				case 'session_mode_required':
					return 'This setup could not be saved because it has no model mode. Try again.';
				case 'session_mode_unknown':
					return 'This model no longer has that mode, so the setup cannot be saved.';
				case 'session_mode_not_offered':
					return 'This model no longer offers this tool, so the setup cannot be saved.';
				default:
					return failure.message ?? 'The setup was not saved.';
			}
		default:
			return 'The setup was not saved.';
	}
}

export function saveErrorCode(error: unknown): string | null {
	const failure = describeEditorTaskFailure(error);
	return failure.kind === 'rejected' ? failure.code : null;
}
