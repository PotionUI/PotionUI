const WEIGHT_SYNTAX = /\([^()]*:\s*-?\d+(\.\d+)?\)/;

export function hasPromptWeights(text: string | null | undefined): boolean {
	return !!text && text.includes(':') && WEIGHT_SYNTAX.test(text);
}

export function promptWeightsSupported(vars: Record<string, unknown> | null | undefined): boolean {
	return vars?.supports_prompt_weights !== false;
}
