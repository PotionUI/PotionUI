export const MODEL_FILTER_SEPARATOR = '\n';

export interface HistoryModelFacet {
	name: string;
	label?: string | null;
	count: number;
}

export interface HistoryModelOption {
	value: string;
	label: string;
	count: number;
}

export function modelFileStem(name: string): string {
	const base = name.replace(/\\/g, '/').split('/').pop() ?? name;
	const dot = base.lastIndexOf('.');
	if (dot > 0 && /^[A-Za-z0-9]{1,12}$/.test(base.slice(dot + 1)) && /[A-Za-z]/.test(base.slice(dot + 1))) {
		return base.slice(0, dot);
	}
	return base;
}

export function buildModelOptions(models: HistoryModelFacet[]): HistoryModelOption[] {
	const byLabel = new Map<string, { names: string[]; count: number }>();
	for (const model of models) {
		if (!model.name) continue;
		const label = model.label?.trim() || modelFileStem(model.name);
		const entry = byLabel.get(label) ?? { names: [], count: 0 };
		if (!entry.names.includes(model.name)) entry.names.push(model.name);
		entry.count += model.count;
		byLabel.set(label, entry);
	}
	return [...byLabel.entries()]
		.map(([label, entry]) => ({
			value: entry.names.join(MODEL_FILTER_SEPARATOR),
			label,
			count: entry.count
		}))
		.sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: 'base', numeric: true }));
}

export function modelFilterLabel(
	value: string,
	options: HistoryModelOption[],
	known: Record<string, string> = {}
): string {
	if (!value) return '';
	const option = options.find((o) => o.value === value);
	if (option) return option.label;
	const names = value.split(MODEL_FILTER_SEPARATOR).filter(Boolean);
	const hit = options.find((o) => names.every((n) => o.value.split(MODEL_FILTER_SEPARATOR).includes(n)));
	if (hit) return hit.label;
	return known[value] || names.map(modelFileStem).join(', ');
}
