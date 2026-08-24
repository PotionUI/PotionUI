export interface FieldConfigOption {
	name: string;
	param_type?: string;
	default?: unknown;
	description?: string;
	required?: boolean;
	choices?: unknown[] | null;
	example?: unknown;
	[key: string]: unknown;
}

export interface FieldTypeEntry {
	type: string;
	component?: string;
	has_options?: boolean;
	container?: boolean;
	source?: string;
	configuration_schema?: FieldConfigOption[];
	[key: string]: unknown;
}

export function configurationOptions(entry: FieldTypeEntry): FieldConfigOption[] {
	return entry.configuration_schema ?? [];
}

/** The registry defaults every built-in type to `source: "core"` - anything else names a plugin. */
export function isPluginSource(source: string | undefined): boolean {
	return !!source && source !== 'core';
}

export function matchesFieldType(entry: FieldTypeEntry, query: string): boolean {
	const needle = query.toLowerCase();
	if (entry.type.toLowerCase().includes(needle)) return true;
	return configurationOptions(entry).some((option) => option.name.toLowerCase().includes(needle));
}
