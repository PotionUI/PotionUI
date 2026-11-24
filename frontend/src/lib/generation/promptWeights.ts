const WEIGHT_SYNTAX = /\([^()]*:\s*-?\d+(\.\d+)?\)/;

export function hasPromptWeights(text: string | null | undefined): boolean {
	return !!text && text.includes(':') && WEIGHT_SYNTAX.test(text);
}

export function promptWeightsSupported(vars: Record<string, unknown> | null | undefined): boolean {
	return vars?.supports_prompt_weights !== false;
}

function hasPicture(value: unknown): boolean {
	if (value == null || value === '') return false;
	if (Array.isArray(value)) return value.some(hasPicture);
	if (typeof value === 'object') {
		const entry = value as Record<string, unknown>;
		return !!(entry.path || entry.relative_path || entry.url);
	}
	return true;
}

export function promptWeightsIgnoredWithPictures(
	vars: Record<string, unknown> | null | undefined,
	mode: string | null | undefined,
	formData: Record<string, unknown> | null | undefined
): boolean {
	const declared = vars?.prompt_weights_ignored_with_images;
	if (!mode || !formData || !Array.isArray(declared)) return false;
	return declared.some((entry) => {
		if (!entry || typeof entry !== 'object') return false;
		const { mode: declaredMode, fields } = entry as { mode?: unknown; fields?: unknown };
		return (
			declaredMode === mode &&
			Array.isArray(fields) &&
			fields.some((field) => typeof field === 'string' && hasPicture(formData[field]))
		);
	});
}
