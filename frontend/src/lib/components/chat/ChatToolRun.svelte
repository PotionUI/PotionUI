<script lang="ts">
	import { slide } from 'svelte/transition';
	import type { ToolExecution } from '$lib/types/chat';
	import { chatModes } from '$lib/stores/chatModes';
	import Icon from '$lib/components/Icon.svelte';

	export let executions: ToolExecution[] = [];

	$: visible = executions.filter((e) => e.approval_status == null);

	function humanize(name: string): string {
		return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
	}

	function toolLabel(execution: ToolExecution): string {
		const info = $chatModes.toolsCatalog.find((t) => t.name === execution.tool_name);
		return info?.label || humanize(execution.tool_name);
	}

	function isFailed(execution: ToolExecution): boolean {
		return execution.status !== 'running' && !execution.skipped && !execution.result.success;
	}

	function formatDuration(ms: number): string {
		if (ms == null) return '';
		return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
	}

	function openOnFailure(node: HTMLDetailsElement, failed: boolean) {
		let applied = false;
		const apply = (next: boolean) => {
			if (!next || applied) return;
			applied = true;
			node.open = true;
		};
		apply(failed);
		return { update: apply };
	}

	$: anyFailed = visible.some(isFailed);
	$: namesLine = visible.map(toolLabel).join(' · ');
</script>

{#if visible.length > 0}
	<!-- Defaults open when a failed call needs surfacing, collapsed otherwise.
	     Markup ported verbatim from the mock's .tool-run (chat-rework BRIEF2). -->
	<details class="tool-run" class:failed={anyFailed} use:openOnFailure={anyFailed}>
		<summary>
			<span class="tool-run-status"><Icon name={anyFailed ? 'chat-close' : 'chat-check'} className="icon" strokeWidth={1.8} /></span>
			<strong>{visible.length} tool{visible.length === 1 ? '' : 's'} used</strong>
			<span class="tool-run-names">{namesLine}</span>
			<Icon name="chat-chevron" className="icon tool-run-chevron" strokeWidth={1.8} />
		</summary>
		<div class="tool-run-details" transition:slide={{ duration: 150 }}>
			{#each visible as execution}
				{@const failed = isFailed(execution)}
				{@const running = execution.status === 'running'}
				{@const skipped = !!execution.skipped}
				<div class="tool-execution" class:failed class:skipped>
					{#if running}
						<span class="tool-execution-spinner" role="status" aria-label="Running"></span>
					{:else if failed}
						<Icon name="chat-close" className="icon" strokeWidth={1.8} />
					{:else if skipped}
						<Icon name="chat-chevron" className="icon" strokeWidth={1.8} />
					{:else}
						<Icon name="chat-check" className="icon" strokeWidth={1.8} />
					{/if}
					<strong>{toolLabel(execution)}</strong>
					<span>{skipped ? 'Not run' : formatDuration(execution.duration_ms)}</span>
				</div>
				{#if failed && execution.result?.error}
					<div class="tool-execution-error" transition:slide={{ duration: 120 }}>{execution.result.error}</div>
				{/if}
			{/each}
		</div>
	</details>
{/if}
