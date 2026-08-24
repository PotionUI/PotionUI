<script lang="ts">
	// Header band for the composed generation detail page: identity + status
	// at a glance, the run's time bounds, and - when it failed - the error
	// surfaced immediately rather than buried in the status log below.
	import type { AdminGenerationListItem } from '$lib/services/admin-api';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { formatDurationMs } from '$lib/components/generation-panel/barState';
	import { Badge } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';

	let { generation, username }: { generation: AdminGenerationListItem; username: string } = $props();

	const STATUS_VARIANT: Record<string, 'neutral' | 'success' | 'warning' | 'danger' | 'info'> = {
		completed: 'success',
		running: 'info',
		pending: 'neutral',
		failed: 'danger',
		cancelled: 'warning'
	};

	function absolute(iso: string | undefined): string {
		if (!iso) return '-';
		const date = new Date(iso);
		if (Number.isNaN(date.getTime())) return '-';
		return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' });
	}

	let durationMs = $derived.by(() => {
		if (!generation.completed_at) return null;
		const ms = new Date(generation.completed_at).getTime() - new Date(generation.created_at).getTime();
		return Number.isFinite(ms) && ms >= 0 ? ms : null;
	});
</script>

<div class="bg-surface-1 border border-line rounded-lg p-4 sm:p-5">
	<div class="flex flex-wrap items-start justify-between gap-4">
		<div class="min-w-0">
			<div class="flex items-center gap-2 flex-wrap">
				<Badge variant={STATUS_VARIANT[generation.status] ?? 'neutral'} size="sm" dot class="uppercase">
					{generation.status}
				</Badge>
				<h2 class="text-base font-semibold text-fg truncate">
					{generation.preset_name || generation.mode || 'Untitled generation'}
				</h2>
				{#if generation.mode && generation.preset_name}
					<span class="text-xs text-fg-subtle font-mono">{generation.mode}</span>
				{/if}
			</div>

			<div class="flex items-center gap-1.5 mt-2 text-xs text-fg-muted">
				<Icon name="user" className="w-3 h-3 text-fg-subtle" />
				<span>{username}</span>
				<span class="text-fg-disabled px-0.5">·</span>
				<span class="font-mono tabular-nums" title={absolute(generation.created_at)}>
					{absolute(generation.created_at)}
				</span>
				<span class="text-fg-subtle">({timeAgo(generation.created_at)})</span>
				<Icon name="chevron-right" className="w-3 h-3 text-fg-disabled" />
				{#if generation.completed_at}
					<span class="font-mono tabular-nums" title={absolute(generation.completed_at)}>
						{absolute(generation.completed_at)}
					</span>
					<span class="text-fg-subtle">({timeAgo(generation.completed_at)})</span>
				{:else}
					<span class="text-fg-subtle italic">{generation.status === 'running' ? 'in progress' : 'never completed'}</span>
				{/if}
			</div>
		</div>

		<div class="flex flex-col items-end gap-1 flex-shrink-0">
			<span class="font-mono tabular-nums text-2xl font-semibold text-fg leading-none">
				{durationMs !== null ? formatDurationMs(durationMs) : '-'}
			</span>
			<span class="font-mono text-2xs text-fg-subtle" title={generation.id}>{generation.id}</span>
		</div>
	</div>

	{#if generation.status === 'failed' && generation.error_message}
		<div class="mt-4 flex items-start gap-2 bg-danger/10 border border-danger/25 rounded p-3">
			<Icon name="warning" className="w-4 h-4 text-danger flex-shrink-0 mt-0.5" />
			<p class="text-xs text-danger leading-relaxed">{generation.error_message}</p>
		</div>
	{/if}
</div>
