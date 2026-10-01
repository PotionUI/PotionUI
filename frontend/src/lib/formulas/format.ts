type OptionSource = { options?: Array<{ label: string; value: unknown }>; type?: string } | undefined;

export function loraKey(row: unknown): string {
	if (!row || typeof row !== 'object') return String(row);
	const r = row as Record<string, unknown>;
	const key = r.model ?? r.modelPath ?? r.path ?? r.id ?? r.name;
	return String(key ?? JSON.stringify(row));
}

export function loraName(row: unknown): string {
	if (!row || typeof row !== 'object') return String(row);
	const r = row as Record<string, unknown>;
	const named = r.name ?? r.label;
	if (typeof named === 'string' && named) return named;
	const key = loraKey(row);
	const tail = key.split(/[\\/]/).pop() ?? key;
	return tail.replace(/^model:/, '').replace(/\.(safetensors|ckpt|pt|pth|bin)$/i, '');
}

export function loraStrength(row: unknown): number | null {
	const strength = (row as { strength?: unknown } | null)?.strength;
	return typeof strength === 'number' ? strength : null;
}

function formatNumber(value: number): string {
	return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(3)));
}

export function formatValue(field: OptionSource, value: unknown): string {
	if (value === undefined || value === null || value === '') return 'None';
	if (typeof value === 'boolean') return value ? 'On' : 'Off';
	if (typeof value === 'number') return formatNumber(value);
	if (Array.isArray(value)) {
		if (value.length === 0) return 'None';
		if (field?.type === 'lora_picker') {
			return value
				.map((row) => {
					const strength = loraStrength(row);
					return strength === null ? loraName(row) : `${loraName(row)} ${formatNumber(strength)}`;
				})
				.join(', ');
		}
		return value.map((item) => formatValue(undefined, item)).join(', ');
	}
	if (typeof value === 'object') {
		return Object.entries(value as Record<string, unknown>)
			.map(([key, item]) => `${key} ${formatValue(undefined, item)}`)
			.join(' · ');
	}
	const text = String(value);
	const option = field?.options?.find((candidate) => String(candidate.value) === text);
	if (option) return option.label;
	return text.startsWith('model:') ? text.slice(6) : text;
}
