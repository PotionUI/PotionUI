export interface RawSelectOption {
	value: unknown;
	label: string;
	sub_label?: string;
	[key: string]: unknown;
}

export interface FormattedSelectOption {
	value: unknown;
	label: string;
	description?: string;
}

export function formatSelectOptions(
	options: RawSelectOption[],
	allowEmpty: boolean
): FormattedSelectOption[] {
	const opts: FormattedSelectOption[] = options.map((opt) => ({
		value: opt.value,
		label: opt.label,
		description: opt.sub_label || undefined
	}));

	if (allowEmpty) {
		opts.unshift({ value: '', label: '-- None --', description: undefined });
	}

	return opts;
}
