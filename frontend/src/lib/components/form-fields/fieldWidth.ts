/** Reads the shared `full_width` field key (top-level, alongside `width`) per
 * the field-schema contract. */
export function isFullWidth(config: unknown): boolean {
	return typeof config === 'object' && config !== null && (config as { full_width?: unknown }).full_width === true;
}
