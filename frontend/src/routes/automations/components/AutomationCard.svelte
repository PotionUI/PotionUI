<script lang="ts">
	import { goto } from '$app/navigation';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge, Button, Card, IconButton } from '$lib/components/ui';
	import RunStatusBadge from '$lib/components/automation/RunStatusBadge.svelte';
	import { timeAgo } from '$lib/utils/relativeTime';
	import type { Automation } from '$lib/types/automations';

	let {
		automation,
		onToggleEnabled,
		onRunNow,
		onDelete
	}: {
		automation: Automation;
		onToggleEnabled: (automation: Automation) => void;
		onRunNow: (automation: Automation) => void;
		onDelete: (automation: Automation) => void;
	} = $props();

	function open() {
		goto(`/automations/${automation.id}`);
	}
</script>

<Card padding="md" class="flex flex-col gap-3">
	<div class="flex items-start justify-between gap-2">
		<button type="button" class="text-left min-w-0" onclick={open}>
			<h3 class="text-sm font-semibold text-fg truncate hover:text-signal transition-colors">
				{automation.name}
			</h3>
			{#if automation.description}
				<p class="text-xs text-fg-muted mt-0.5 line-clamp-2">{automation.description}</p>
			{/if}
		</button>
		<IconButton icon="trash" label="Delete automation" size="sm" onclick={() => onDelete(automation)} />
	</div>

	<div class="flex items-center flex-wrap gap-2">
		<Badge variant={automation.enabled ? 'success' : 'neutral'} size="sm" dot>
			{automation.enabled ? 'Enabled' : 'Disabled'}
		</Badge>
		<RunStatusBadge status={automation.last_run_status} dot={false} />
		{#if automation.last_run_at}
			<span class="text-2xs font-mono tabular-nums text-fg-subtle">{timeAgo(automation.last_run_at)}</span>
		{/if}
	</div>

	<div class="flex items-center gap-2 mt-auto pt-1">
		<Button variant="secondary" size="sm" icon="play" onclick={() => onRunNow(automation)}>
			Run Now
		</Button>
		<Button variant="ghost" size="sm" onclick={() => onToggleEnabled(automation)}>
			{automation.enabled ? 'Disable' : 'Enable'}
		</Button>
		<Button variant="ghost" size="sm" icon="edit" onclick={open}>Edit</Button>
	</div>
</Card>
