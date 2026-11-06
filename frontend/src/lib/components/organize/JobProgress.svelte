<script lang="ts">
	import { onDestroy } from 'svelte';
	import { Alert, Button, Spinner } from '$lib/components/ui';
	import { useOrganizeApi } from '$lib/organize/scope';
	import { jobIsActive, jobPercent } from '$lib/organize/jobs';
	import type { OrganizeJob } from '$lib/types/organize';

	const api = useOrganizeApi();

	let {
		job,
		pollMs = 1000,
		onupdate,
		onviewactivity
	}: {
		job: OrganizeJob;
		pollMs?: number;
		onupdate?: (job: OrganizeJob) => void;
		onviewactivity?: () => void;
	} = $props();

	let current = $state<OrganizeJob>(job);
	let cancelling = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;
	let stopped = false;

	const percent = $derived(jobPercent(current));
	const active = $derived(jobIsActive(current));
	const instance = $derived(current.scope === 'instance');

	async function poll() {
		if (stopped) return;
		try {
			const response = await api.getOrganizeJob(current.id);
			if (response.success && response.data) {
				current = response.data;
				onupdate?.(current);
			}
		} catch {
			current = current;
		}
		if (!stopped && jobIsActive(current)) timer = setTimeout(poll, pollMs);
	}

	$effect(() => {
		current = job;
		clearTimeout(timer);
		if (jobIsActive(job)) timer = setTimeout(poll, pollMs);
	});

	onDestroy(() => {
		stopped = true;
		clearTimeout(timer);
	});

	async function cancel() {
		cancelling = true;
		try {
			const response = await api.cancelOrganizeJob(current.id);
			if (response.success && response.data) {
				current = response.data;
				onupdate?.(current);
			}
		} finally {
			cancelling = false;
		}
	}
</script>

<div class="space-y-3" data-testid="job-progress" data-status={current.status}>
	{#if active}
		<div class="flex items-center gap-2 text-sm text-fg">
			<Spinner size="sm" />
			<span>
				{#if instance}Running {current.rule_name} on existing models{:else}Adding existing items to {current.rule_name}{/if}
			</span>
		</div>
		<div class="h-1.5 overflow-hidden rounded bg-surface-3" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={percent}>
			<div class="h-full bg-signal transition-[width]" style="width: {percent}%"></div>
		</div>
		{#if instance}
			<dl class="grid grid-cols-3 gap-2 text-[12px]" data-testid="job-breakdown">
				<div class="rounded border border-line bg-surface-2 px-2 py-1.5">
					<dt class="text-fg-subtle">Updated</dt>
					<dd class="font-mono text-sm tabular-nums text-fg">{current.applied}</dd>
				</div>
				<div class="rounded border border-line bg-surface-2 px-2 py-1.5">
					<dt class="text-fg-subtle">Skipped</dt>
					<dd class="font-mono text-sm tabular-nums text-fg">{current.skipped ?? 0}</dd>
				</div>
				<div class="rounded border border-line bg-surface-2 px-2 py-1.5">
					<dt class="text-fg-subtle">Waiting</dt>
					<dd class="font-mono text-sm tabular-nums {(current.waiting ?? 0) > 0 ? 'text-warning' : 'text-fg'}">{current.waiting ?? 0}</dd>
				</div>
			</dl>
		{/if}
		<div class="flex items-center justify-between">
			<span class="font-mono text-xs tabular-nums text-fg-muted">{current.processed} of {current.total} checked</span>
			<Button size="sm" variant="secondary" loading={cancelling} onclick={cancel}>Stop</Button>
		</div>
	{:else if current.status === 'completed'}
		<Alert variant="success" icon density="compact">
			{#if instance}
				Finished {current.rule_name}: {current.applied} updated{#if current.skipped}, {current.skipped} skipped{/if}{#if current.waiting}, {current.waiting} waiting to be tried again{/if}.
			{:else}
				Added {current.applied} {current.applied === 1 ? 'item' : 'items'} to {current.rule_name}.
			{/if}
			{#snippet actions()}
				{#if onviewactivity}
					<Button size="sm" variant="ghost" onclick={onviewactivity}>See activity</Button>
				{/if}
			{/snippet}
		</Alert>
	{:else if current.status === 'cancelled'}
		<Alert variant="neutral" icon density="compact">
			Stopped after {current.applied} {current.applied === 1 ? 'item' : 'items'}. You can undo them from Activity.
			{#snippet actions()}
				{#if onviewactivity}
					<Button size="sm" variant="ghost" onclick={onviewactivity}>See activity</Button>
				{/if}
			{/snippet}
		</Alert>
	{:else}
		<Alert variant="danger" icon density="compact">
			{current.error || 'Adding the existing items did not finish.'}
		</Alert>
	{/if}
</div>
