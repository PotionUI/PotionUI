<script lang="ts">
	/**
	 * Collapsible record of a message's completed/failed tool calls, replacing
	 * the always-visible chip strip. Approval is a separate surface
	 * (ApprovalDock docked above the composer) — a pending execution is not
	 * this record's business and is filtered out before anything renders.
	 * A failed row always shows its `result.error` inline (no click-to-reveal):
	 * that's the one place a tool's own text reaches the transcript, so it
	 * must never require a second interaction to see.
	 */
	import { slide } from 'svelte/transition';
	import type { ToolExecution } from '$lib/types/chat';
	import { chatModes } from '$lib/stores/chatModes';

	export let executions: ToolExecution[] = [];

	$: visible = executions.filter((e) => !e.pending_approval);

	function humanize(name: string): string {
		return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
	}

	function toolLabel(execution: ToolExecution): string {
		const info = $chatModes.toolsCatalog.find((t) => t.name === execution.tool_name);
		return info?.label || humanize(execution.tool_name);
	}

	function isFailed(execution: ToolExecution): boolean {
		return execution.status !== 'running' && !execution.rejected && !execution.result.success;
	}

	function formatDuration(ms: number): string {
		if (ms == null) return '';
		return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
	}

	$: anyFailed = visible.some(isFailed);
	$: namesLine = visible.map(toolLabel).join(' · ');
</script>

{#if visible.length > 0}
	<!-- Defaults open when a failed call needs surfacing, collapsed otherwise. -->
	<details class="group my-3 w-full max-w-[500px] overflow-hidden rounded-lg border border-line bg-surface-2 text-fg-subtle" open={anyFailed}>
		<summary class="flex min-h-[34px] cursor-pointer list-none items-center gap-1.5 px-2.5 py-1.5 select-none hover:bg-surface-3/60 [&::-webkit-details-marker]:hidden">
			<span
				class="flex h-[17px] w-[17px] flex-shrink-0 items-center justify-center rounded {anyFailed
					? 'bg-danger/10 text-danger'
					: 'bg-success/10 text-success'}"
			>
				{#if anyFailed}
					<svg class="h-2.5 w-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.3" d="M6 18L18 6M6 6l12 12" />
					</svg>
				{:else}
					<svg class="h-2.5 w-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.3" d="M5 13l4 4L19 7" />
					</svg>
				{/if}
			</span>
			<strong class="whitespace-nowrap font-mono text-2xs font-semibold text-fg-muted"
				>{visible.length} tool{visible.length === 1 ? '' : 's'} used</strong
			>
			<span class="min-w-0 flex-1 truncate font-mono text-2xs text-fg-subtle">{namesLine}</span>
			<svg
				class="h-3 w-3 flex-shrink-0 transition-transform group-open:rotate-180"
				fill="none"
				stroke="currentColor"
				viewBox="0 0 24 24"
			>
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
			</svg>
		</summary>
		<div class="border-t border-line px-1.5 py-1.5" transition:slide={{ duration: 150 }}>
			{#each visible as execution}
				{@const failed = isFailed(execution)}
				{@const running = execution.status === 'running'}
				{@const rejected = !!execution.rejected}
				<div
					class="grid grid-cols-[14px_minmax(0,1fr)_auto] items-center gap-1.5 rounded px-1.5 py-1 hover:bg-surface-3/50 {failed
						? 'text-danger'
						: rejected
							? 'text-fg-disabled'
							: 'text-fg-muted'}"
				>
					<span class="flex items-center justify-center">
						{#if running}
							<span
								class="h-3 w-3 rounded-full border-2 border-line-strong animate-spin"
								style="border-top-color: rgb(var(--accent));"
								role="status"
								aria-label="Running"
							></span>
						{:else if failed}
							<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M6 18L18 6M6 6l12 12" />
							</svg>
						{:else if rejected}
							<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M5 12h14" />
							</svg>
						{:else}
							<svg class="h-3 w-3 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M5 13l4 4L19 7" />
							</svg>
						{/if}
					</span>
					<strong class="truncate text-2xs font-medium {rejected ? 'line-through' : ''}"
						>{toolLabel(execution)}</strong
					>
					<span class="font-mono tabular-nums text-2xs text-fg-subtle">{formatDuration(execution.duration_ms)}</span>
				</div>
				{#if failed && execution.result?.error}
					<div class="mb-1 ml-[26px] text-2xs text-danger" transition:slide={{ duration: 120 }}>
						{execution.result.error}
					</div>
				{/if}
			{/each}
		</div>
	</details>
{/if}
