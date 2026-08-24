/**
 * DynamicForm flattens into a new object for every reactive pass. Compare the
 * serialized payload, not object identity, before forwarding it to a parent.
 */
export function shouldPublishFormData(lastPublished: string | null, nextData: Record<string, unknown>): boolean {
	return JSON.stringify(nextData) !== lastPublished;
}
