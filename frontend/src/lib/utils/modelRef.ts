/**
 * Stable-reference matching for a model-valued form field. A field stores
 * `model:<id>` once a model is selected; `matchesStoredValue` also accepts
 * the legacy `file_path`/bare-filename values sessions saved before that
 * migration still carry. Shared by ModelField.svelte and LoraPickerField.svelte
 * (previously duplicated verbatim in both).
 */

export const MODEL_REF_PREFIX = 'model:';

export interface ModelRefCandidate {
	id?: string | null;
	file_path?: string | null;
	filename?: string | null;
}

/** The value a field should store once `model` is selected. */
export function refFor(model: ModelRefCandidate | null | undefined): string {
	if (!model) return '';
	return model.id ? `${MODEL_REF_PREFIX}${model.id}` : model.file_path || '';
}

/** Does a stored field value refer to this model? */
export function matchesStoredValue(
	model: ModelRefCandidate | null | undefined,
	storedValue: string | null | undefined
): boolean {
	if (!model || !storedValue) return false;
	if (storedValue.startsWith(MODEL_REF_PREFIX)) {
		return model.id === storedValue.slice(MODEL_REF_PREFIX.length);
	}
	if (model.file_path === storedValue) return true;
	const filename = storedValue.split('/').pop();
	return !!filename && model.filename === filename;
}

/** First candidate in `list` that `storedValue` resolves to, if any. */
export function findModelForValue<T extends ModelRefCandidate>(
	storedValue: string | null | undefined,
	list: T[]
): T | undefined {
	return list.find((m) => matchesStoredValue(m, storedValue));
}
