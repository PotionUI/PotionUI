/**
 * Shared "what name do we show a user for this model" helper.
 *
 * The API now computes and returns a `name` field for every model payload
 * (custom name -> provider name -> filename without extension). Prefer it
 * everywhere; the extra fallbacks only matter defensively, e.g. against a
 * stale cached row that predates the `name` field.
 *
 * `filename` must never be used as the primary display value - it's a
 * matching key for legacy stored session values, not something to show a
 * generating user.
 */
export function modelDisplayName(model: {
	name?: string | null;
	custom_name?: string | null;
	providers?: Array<{ name?: string | null }> | null;
	filename?: string | null;
} | null | undefined): string {
	if (!model) return '';
	return (
		model.name ||
		model.custom_name ||
		model.providers?.[0]?.name ||
		model.filename ||
		''
	);
}
