<script lang="ts">
	import { confirmDialog } from '$lib/stores/confirm';
	import { toasts } from '$lib/stores/toast';
	import { logger } from '$lib/utils/logger';
	import { cancelMediaToolPoolJob } from '$lib/services/api/mediaToolsPool';
	import { Badge, EmptyState, IconButton } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import { formatWait, POOL_MAX_ROWS, type PoolRow } from './mediaToolsPool';

	let {
		rows,
		onChanged
	}: {
		rows: PoolRow[];
		onChanged: () => void;
	} = $props();

	let cancelling = $state<Record<string, boolean>>({});

	const visible = $derived(rows.slice(0, POOL_MAX_ROWS));
	const hidden = $derived(Math.max(0, rows.length - POOL_MAX_ROWS));

	async function cancel(row: PoolRow) {
		const confirmed = await confirmDialog({
			title: row.state === 'running' ? 'Stop this job?' : 'Remove this job from the queue?',
			message: `${row.name} started by ${row.user} will be cancelled.`,
			variant: 'danger'
		});
		if (!confirmed) return;
		cancelling = { ...cancelling, [row.id]: true };
		try {
			await cancelMediaToolPoolJob(row.id);
		} catch (error) {
			logger.error('Failed to cancel the media tool job:', error);
			toasts.error(error instanceof Error ? error.message : 'Could not cancel the job.');
		} finally {
			cancelling = { ...cancelling, [row.id]: false };
			onChanged();
		}
	}
</script>

<DetailSection label="Processing now" padded={false}>
	{#snippet headerExtra()}
		<span class="font-mono text-xs tabular-nums text-fg-subtle" data-pool-jobs-count>{rows.length}</span>
	{/snippet}

	{#if rows.length === 0}
		<EmptyState
			icon="loading"
			title="Nothing is processing"
			description="Crop, trim, resize and effect jobs from every user show up here while they run."
			compact
		/>
	{:else}
		<div class="overflow-x-auto" data-pool-jobs>
			<table class="w-full min-w-[40rem] text-sm">
				<thead>
					<tr class="border-b border-line text-left text-xs text-fg-subtle">
						<th class="px-4 sm:px-5 py-2 font-medium">Job</th>
						<th class="px-2 py-2 font-medium">User</th>
						<th class="px-2 py-2 font-medium">State</th>
						<th class="px-2 py-2 font-medium">Progress</th>
						<th class="px-2 py-2 text-right font-medium">Time</th>
						<th class="px-4 sm:px-5 py-2"><span class="sr-only">Actions</span></th>
					</tr>
				</thead>
				<tbody class="divide-y divide-line">
					{#each visible as row (row.id)}
						<tr data-pool-job={row.id}>
							<td class="px-4 sm:px-5 py-2.5">
								<div class="flex items-center gap-2">
									<span class="truncate font-medium text-fg">{row.name}</span>
									{#if row.kind === 'preview'}
										<Badge size="sm">Preview</Badge>
									{/if}
								</div>
							</td>
							<td class="px-2 py-2.5 text-fg-muted">{row.user}</td>
							<td class="px-2 py-2.5">
								{#if row.state === 'running'}
									<Badge variant="info" size="sm" dot>Running</Badge>
								{:else}
									<Badge size="sm" class="font-mono tabular-nums">Queued #{row.position}</Badge>
								{/if}
							</td>
							<td class="px-2 py-2.5">
								{#if row.state === 'running' && row.progress != null}
									<div class="flex items-center gap-2">
										<div
											class="h-1.5 w-24 overflow-hidden rounded-full bg-surface-3"
											role="progressbar"
											aria-valuemin="0"
											aria-valuemax="100"
											aria-valuenow={row.progress}
											aria-label="Progress"
										>
											<div class="h-full bg-signal transition-[width]" style="width: {row.progress}%"></div>
										</div>
										<span class="font-mono text-xs tabular-nums text-fg-muted">{row.progress}%</span>
									</div>
								{:else}
									<span class="font-mono text-xs text-fg-subtle">{row.state === 'running' ? 'Working' : 'Waiting'}</span>
								{/if}
							</td>
							<td class="px-2 py-2.5 text-right font-mono text-xs tabular-nums text-fg-muted">
								{row.state === 'running' ? 'Running' : 'Waited'} {formatWait(row.seconds)}
							</td>
							<td class="px-4 sm:px-5 py-1.5 text-right">
								<IconButton
									icon="close"
									label={row.state === 'running' ? 'Stop job' : 'Remove from queue'}
									tooltip={row.state === 'running' ? 'Stop job' : 'Remove from queue'}
									size="sm"
									loading={!!cancelling[row.id]}
									onclick={() => cancel(row)}
								/>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if hidden > 0}
			<p class="border-t border-line px-4 sm:px-5 py-2 font-mono text-xs tabular-nums text-fg-subtle">
				Showing {POOL_MAX_ROWS} of {rows.length}
			</p>
		{/if}
	{/if}
</DetailSection>
