/**
 * A text-typed provider option stays text unless the WHOLE value is a numeric
 * literal. Ollama durations ("5m", "1h") begin with digits, so `parseFloat`
 * silently sends 5 and 1.
 */
export function coerceProviderOptionText(raw: string): string | number {
	const trimmed = raw.trim();
	if (trimmed === '') return raw;
	const asNumber = Number(trimmed);
	return Number.isFinite(asNumber) ? asNumber : raw;
}
