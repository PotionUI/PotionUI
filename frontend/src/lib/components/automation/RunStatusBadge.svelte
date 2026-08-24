<script lang="ts">
	/**
	 * Shared status pill for automation runs and run nodes. Covers both
	 * `RunStatus` (running/success/failed/cancelled) and `NodeRunStatus`
	 * (running/success/failed/skipped/waiting), plus 'completed' as used by
	 * some node-status messages - see src/lib/types/automations.ts.
	 *
	 * `animated` is the graph-canvas treatment from B5 (running=pulse,
	 * skipped=dimmed) - opt in for that context, off by default elsewhere.
	 */
	import { Badge } from '$lib/components/ui';

	type Status =
		| 'running'
		| 'success'
		| 'completed'
		| 'failed'
		| 'skipped'
		| 'waiting'
		| 'cancelled';

	let {
		status,
		dot = true,
		animated = false
	}: {
		status?: Status | null;
		dot?: boolean;
		animated?: boolean;
	} = $props();

	const variantByStatus: Record<Status, 'signal' | 'success' | 'danger' | 'neutral' | 'warning'> = {
		running: 'signal',
		success: 'success',
		completed: 'success',
		failed: 'danger',
		skipped: 'neutral',
		waiting: 'warning',
		cancelled: 'neutral'
	};
</script>

{#if status}
	<Badge
		variant={variantByStatus[status] ?? 'neutral'}
		size="sm"
		{dot}
		class={animated && status === 'running' ? 'animate-pulse' : animated && status === 'skipped' ? 'opacity-60' : ''}
	>
		<span class="font-mono uppercase">{status}</span>
	</Badge>
{/if}
