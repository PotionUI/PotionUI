<script lang="ts">
	/**
	 * One tool execution, compressed to a status chip in the transcript.
	 * Approval itself lives in ApprovalDock — a pending chip only shows the
	 * warning dot and is otherwise inert. A failed chip is the one interactive
	 * case: clicking it toggles a one-line `result.error` beneath the chip row.
	 */
	import type { ToolExecution } from '$lib/types/chat';

	export let execution: ToolExecution;
	export let toolMeta: { icon?: string | null; label?: string | null } | null = null;
	export let expanded: boolean = false;
	export let onToggle: (() => void) | undefined = undefined;

	$: label =
		toolMeta?.label ||
		execution.tool_name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
	$: running = execution.status === 'running';
	$: pending = !!execution.pending_approval;
	$: rejected = !!execution.rejected;
	$: failed = !running && !pending && !rejected && !execution.result.success;
	$: clickable = failed && !!execution.result?.error;
</script>

<button
	type="button"
	class="inline-flex items-center gap-1.5 h-6 px-1.5 rounded border font-mono text-xs transition-colors {failed
		? 'border-danger/40 text-danger hover:bg-danger/10'
		: rejected
			? 'border-line text-fg-disabled line-through'
			: 'border-line text-fg-muted'} {clickable ? 'cursor-pointer' : 'cursor-default'}"
	disabled={!clickable}
	aria-expanded={clickable ? expanded : undefined}
	on:click={() => clickable && onToggle?.()}
>
	{#if running}
		<span
			class="w-3 h-3 rounded-full border-2 border-line-strong animate-spin flex-shrink-0"
			style="border-top-color: rgb(var(--accent));"
			role="status"
			aria-label="Running"
		></span>
	{:else if pending}
		<span
			class="w-1.5 h-1.5 rounded-full bg-warning motion-safe:animate-pulse flex-shrink-0"
			title="Awaiting approval"
		></span>
	{:else if failed || rejected}
		<span class="w-1.5 h-1.5 rounded-full bg-danger flex-shrink-0" title={rejected ? 'Rejected' : 'Failed'}
		></span>
	{:else}
		<span class="w-1.5 h-1.5 rounded-full bg-success flex-shrink-0" title="Completed"></span>
	{/if}
	<span class="truncate max-w-[160px]">{label}</span>
</button>
