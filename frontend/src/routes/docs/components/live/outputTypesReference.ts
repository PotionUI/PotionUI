export interface OutputTypeField {
	name: string;
	type: string;
	default?: unknown;
	[key: string]: unknown;
}

export interface OutputTypeEntry {
	key: string;
	output_class?: string;
	message_type?: string;
	has_handler?: boolean;
	has_serializer?: boolean;
	description?: string;
	fields?: OutputTypeField[];
	[key: string]: unknown;
}

export function outputTypeFields(entry: OutputTypeEntry): OutputTypeField[] {
	return entry.fields ?? [];
}

/** The API sends the literal string "<dynamic>" for a spec whose message_type
 * is derived per-instance from a callable rather than fixed. */
export function isDynamicMessageType(messageType: string | undefined): boolean {
	return messageType === '<dynamic>';
}

export function matchesOutputType(entry: OutputTypeEntry, query: string): boolean {
	const needle = query.toLowerCase();
	if (entry.key.toLowerCase().includes(needle)) return true;
	if ((entry.output_class || '').toLowerCase().includes(needle)) return true;
	if ((entry.description || '').toLowerCase().includes(needle)) return true;
	return outputTypeFields(entry).some((field) => field.name.toLowerCase().includes(needle));
}
