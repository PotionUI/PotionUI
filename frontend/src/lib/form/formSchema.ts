import type { FieldConfig } from './reactions';

export interface FormSchema {
	properties: Record<string, FieldConfig>;
}

export interface BuildFormSchemaOptions {
	columns?: number;
	rootName?: string;
}

export function buildFormSchema(
	fields: FieldConfig[],
	options: BuildFormSchemaOptions = {}
): FormSchema {
	const rootName = options.rootName ?? 'fields';
	return {
		properties: {
			[rootName]: {
				type: 'row',
				name: rootName,
				configuration: { columns: options.columns ?? 1 },
				children: fields
			}
		}
	};
}
