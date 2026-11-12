<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import type { ModeDownloadProgress } from '$lib/services/api/recipeModes';
	import type { ApprovedModesPlan } from './modeInstallModel';
	import { stepDetailLine } from './modeInstallModel';

	type Group = {
		stepKey: string;
		title: string;
		kind: string;
		status: string;
		latest: { safe_error_detail?: string | null; safe_suggested_action?: string | null; status?: string } | null;
	};

	let {
		groups,
		plan,
		modeCount,
		progress
	}: {
		groups: Group[];
		plan: ApprovedModesPlan | null;
		modeCount: number;
		progress: ModeDownloadProgress | null;
	} = $props();

	function state(status: string): 'done' | 'active' | 'failed' | 'attention' | 'pending' {
		if (status === 'succeeded') return 'done';
		if (status === 'failed') return 'failed';
		if (status === 'action_required') return 'attention';
		if (status === 'running' || status === 'awaiting_consent') return 'active';
		return 'pending';
	}
</script>

<ol class="rounded-lg border border-line bg-surface-1 px-5 py-2" data-mode-steps>
	{#each groups as group (group.stepKey)}
		{@const current = state(group.status)}
		<li class="flex items-start gap-3 py-2.5" data-mode-step={group.stepKey} data-step-state={current}>
			<span
				class="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full border {current === 'done'
					? 'border-success/50 bg-success/10 text-success'
					: current === 'active'
						? 'border-signal text-signal'
						: current === 'failed'
							? 'border-danger text-danger'
							: current === 'attention'
								? 'border-warning text-warning'
								: 'border-line-strong text-fg-subtle'}"
				aria-hidden="true"
			>
				{#if current === 'done'}
					<Icon name="check" className="h-3 w-3" />
				{:else if current === 'active'}
					<span class="h-1.5 w-1.5 rounded-full bg-signal-solid"></span>
				{:else if current === 'failed'}
					<Icon name="close" className="h-3 w-3" />
				{:else if current === 'attention'}
					<Icon name="warning" className="h-3 w-3" />
				{/if}
			</span>
			<span class="min-w-0 flex-1">
				<span class="block text-base {current === 'pending' ? 'text-fg-subtle' : 'text-fg'}">{group.title}</span>
				<span class="block text-sm {current === 'pending' ? 'text-fg-subtle' : 'text-fg-muted'}">
					{stepDetailLine(group.kind, current === 'attention' ? 'failed' : current, plan, modeCount, progress)}
				</span>
				{#if (current === 'failed' || current === 'attention') && group.latest?.safe_error_detail}
					<span class="mt-1 block text-sm {current === 'failed' ? 'text-danger' : 'text-warning'}">
						{group.latest.safe_error_detail}
					</span>
					{#if group.latest.safe_suggested_action}
						<span class="block text-sm text-fg-muted">{group.latest.safe_suggested_action}</span>
					{/if}
				{/if}
			</span>
		</li>
	{/each}
</ol>
