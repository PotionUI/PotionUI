/**
 * Generic one-line summary of a chat tool execution, derived from common
 * result-data shapes (counts of models/prompts/values/...). Deliberately
 * shape-based, not tool-name based — per-tool rendering belongs to
 * `chatToolRendererRegistry` entries.
 */
export interface ToolExecutionLike {
	tool_name: string;
	arguments: Record<string, unknown>;
	result: { success: boolean; data: string; error?: string };
}

export function getToolSummary(exec: ToolExecutionLike): string {
	if (!exec.result.success) return exec.result.error || 'Failed';
	try {
		const data = JSON.parse(exec.result.data);
		if (data.models) return `${data.models.length} model${data.models.length !== 1 ? 's' : ''}`;
		if (data.count !== undefined) return `${data.count} result${data.count !== 1 ? 's' : ''}`;
		if (data.prompts) return `${data.prompts.length} prompt${data.prompts.length !== 1 ? 's' : ''}`;
		if (data.categories)
			return `${data.categories.length} categor${data.categories.length !== 1 ? 'ies' : 'y'}`;
		if (data.values) return `${data.values.length} value${data.values.length !== 1 ? 's' : ''}`;
		if (data.templates)
			return `${data.templates.length} template${data.templates.length !== 1 ? 's' : ''}`;
		if (data.segments)
			return `${data.segments.length} segment${data.segments.length !== 1 ? 's' : ''}`;
		if (data.fields) return `${Object.keys(data.fields).length} fields`;
		if (data.generation_id) return `Started: ${data.generation_id.slice(0, 8)}...`;
		if (data.action) return String(data.action);
		if (data.message) return String(data.message);
		return 'OK';
	} catch {
		return 'OK';
	}
}
