/**
 * Turning a failed edit into something the user can read.
 *
 * `BaseController.error_response` RAISES an HTTPException, so a refused edit
 * arrives as an axios rejection with the useful part buried at
 * `response.data.detail.message` - not as a `{success: false}` body. Reading
 * only the outer error yields "Request failed with status code 400", which
 * tells the user nothing about the crop they drew.
 */

function stringField(value: unknown, key: string): string | null {
	if (!value || typeof value !== 'object') return null;
	const field = (value as Record<string, unknown>)[key];
	return typeof field === 'string' && field.trim() ? field : null;
}

function nested(value: unknown, key: string): unknown {
	if (!value || typeof value !== 'object') return null;
	return (value as Record<string, unknown>)[key];
}

/** The server's own explanation when there is one, and a fallback when not. */
export function describeEditFailure(
	error: unknown,
	fallback = 'The edit could not be applied'
): string {
	const data = nested(nested(error, 'response'), 'data');
	const detail = nested(data, 'detail');

	return (
		stringField(detail, 'message') ??
		(typeof detail === 'string' && detail.trim() ? detail : null) ??
		stringField(data, 'message') ??
		stringField(error, 'message') ??
		fallback
	);
}
