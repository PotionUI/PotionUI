export interface TextArtifactAction {
	label: string;
	field: string;
	values?: Record<string, unknown>;
}

export interface TextArtifactData {
	index?: number;
	title: string;
	text: string;
	mono?: boolean;
	action?: TextArtifactAction | null;
}

export function textArtifactAction(data: TextArtifactData | null | undefined): TextArtifactAction | null {
	const action = data?.action;
	if (!action || typeof action.field !== 'string' || !action.field) return null;
	if (typeof data?.text !== 'string') return null;
	return action;
}

export function textArtifactValues(data: TextArtifactData): Record<string, unknown> | null {
	const action = textArtifactAction(data);
	if (!action) return null;
	return { ...(action.values ?? {}), [action.field]: data.text };
}

export function applyArtifactValues(
	formData: Record<string, unknown> | undefined,
	values: Record<string, unknown>
): Record<string, unknown> {
	return { ...(formData ?? {}), ...values };
}
