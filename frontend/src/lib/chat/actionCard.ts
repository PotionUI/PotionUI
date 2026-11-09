import type {
	ChatToolInfo,
	ToolExecution,
	ToolResolveStatus,
	UnifiedChatMessageData
} from '$lib/types/chat';
import { buildApprovalDiff, deriveCompactSummary } from './approvalPreview';
import { formatNumberVariableSummary } from './conditionText';

export type ActionCardState = 'pending' | 'stale' | 'applied' | 'rejected' | 'failed' | 'expired';

export interface ActionDecision {
	approved: boolean;
	reason?: string;
	force?: boolean;
}

export interface ActionResolveOutcome {
	status: ToolResolveStatus;
	note?: string | null;
}

export interface PendingExecutionRef {
	messageId: string;
	index: number;
	execution: ToolExecution;
}

export const MAX_REJECT_REASON_LENGTH = 2000;

export const ACTION_STATE_LABEL: Record<ActionCardState, string> = {
	pending: 'Needs approval',
	stale: 'Form changed',
	applied: 'Applied',
	rejected: 'Rejected',
	failed: 'Failed',
	expired: 'Expired'
};

export function actionCardState(execution: ToolExecution, stale: boolean): ActionCardState | null {
	switch (execution.approval_status) {
		case 'pending':
			return stale ? 'stale' : 'pending';
		case 'approved':
			return 'applied';
		case 'rejected':
			return 'rejected';
		case 'failed':
			return 'failed';
		case 'expired':
			return 'expired';
		default:
			return null;
	}
}

export function actionExecutions(executions: ToolExecution[]): Array<{ execution: ToolExecution; index: number }> {
	return executions
		.map((execution, index) => ({ execution, index }))
		.filter(({ execution }) => execution.approval_status != null);
}

export function latestPendingExecution(messages: UnifiedChatMessageData[]): PendingExecutionRef | null {
	for (let i = messages.length - 1; i >= 0; i--) {
		const message = messages[i];
		if (message.role !== 'assistant' || !message.id) continue;
		const executions = message.tool_executions || [];
		const index = executions.findIndex((e) => e.approval_status === 'pending');
		return index === -1 ? null : { messageId: message.id, index, execution: executions[index] };
	}
	return null;
}

export function replaceExecution(
	messages: UnifiedChatMessageData[],
	messageId: string,
	index: number,
	execution: ToolExecution
): UnifiedChatMessageData[] {
	return messages.map((m) => {
		if (m.id !== messageId || !m.tool_executions?.[index]) return m;
		const executions = [...m.tool_executions];
		executions[index] = { ...executions[index], ...execution };
		return { ...m, tool_executions: executions };
	});
}

export function markExecutionRejected(
	messages: UnifiedChatMessageData[],
	ref: { messageId: string; index: number },
	reason: string
): UnifiedChatMessageData[] {
	const current = messages.find((m) => m.id === ref.messageId)?.tool_executions?.[ref.index];
	if (!current) return messages;
	return replaceExecution(messages, ref.messageId, ref.index, {
		...current,
		approval_status: 'rejected',
		approval_reason: reason.slice(0, MAX_REJECT_REASON_LENGTH)
	});
}

function humanizeToolName(name: string): string {
	return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function actionToolLabel(toolName: string, catalog: ChatToolInfo[]): string {
	const info = catalog.find((t) => t.name === toolName);
	return info?.label || humanizeToolName(toolName);
}

const NON_IMAGE_MODE = /vid|video|audio|music|speech|sound/i;

export function approveVerb(execution: ToolExecution): string {
	const preview = execution.preview;
	if (preview?.kind !== 'generation') return 'Approve';
	const mode = preview.fields?.find((f) => f.label === 'Mode')?.value ?? '';
	const count = Number.parseInt(preview.fields?.find((f) => f.label === 'Batch')?.value ?? '', 10);
	if (NON_IMAGE_MODE.test(mode) || !Number.isFinite(count) || count < 1) return 'Run';
	return `Run ${count} image${count === 1 ? '' : 's'}`;
}

export function actionCardIcon(execution: ToolExecution): string {
	const kind = execution.preview?.kind;
	if (kind === 'generation') return 'chat-sparkles';
	if (kind === 'text_edit') return 'chat-edit';
	if (kind === 'timeline') return 'chat-clock';
	if (kind === 'regions') return 'chat-sliders';
	if (buildApprovalDiff(execution)) return 'chat-sliders';
	return 'chat-tools';
}

export function collapsedSummary(execution: ToolExecution, toolLabel: string): string {
	const diff = buildApprovalDiff(execution);
	if (diff?.length) return diff.map((row) => `${row.field} ${row.newNumber ? formatNumberVariableSummary(row.newNumber) : row.newValue}`).join(' · ');
	const changed = execution.preview?.fields?.filter((f) => f.old != null) ?? [];
	if (changed.length) return changed.map((f) => `${f.label} ${f.value}`).join(' · ');
	return deriveCompactSummary(execution, toolLabel, execution.preview);
}

const CONTINUATION_ERROR_TEXT: Record<string, string> = {
	tool_execution_pending: 'This action is still waiting for approval, so the reply could not continue.',
	continuation_conflict: 'The conversation has moved on, so the reply could not continue from this action.'
};

export function continuationErrorText(rejection: { message?: string; code?: string }): string {
	return (
		rejection.message ||
		(rejection.code ? CONTINUATION_ERROR_TEXT[rejection.code] : undefined) ||
		'The reply could not continue after this action.'
	);
}

export function redoRequestText(toolLabel: string): string {
	return `Redo "${toolLabel}" against the current form. I changed it after you proposed this.`;
}
