<script lang="ts">
	import { Alert, Badge, Button } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import Icon from '$lib/components/Icon.svelte';
	import { nextSetupStep, setupProgressLabel, type PluginSetupReport, type SetupStep } from '$lib/plugins/setup';

	let {
		report,
		busyStepId = null,
		onAction
	}: {
		report: PluginSetupReport;
		busyStepId?: string | null;
		onAction: (step: SetupStep) => void;
	} = $props();

	const next = $derived(nextSetupStep(report));

	const markerClasses: Record<SetupStep['status'], string> = {
		done: 'border-success/30 bg-success/10 text-success',
		todo: 'border-line-strong bg-surface-2 text-fg',
		waiting: 'border-line bg-surface-1 text-fg-subtle'
	};
</script>

{#snippet actionButton(step: SetupStep, variant: 'primary' | 'secondary')}
	{#if step.action}
		{#if step.action.kind === 'link' && step.action.href}
			<Button {variant} size="sm" href={step.action.href}>{step.action.label}</Button>
		{:else}
			<Button
				{variant}
				size="sm"
				loading={busyStepId === step.id}
				disabled={busyStepId !== null}
				onclick={() => onAction(step)}
			>
				{step.action.label}
			</Button>
		{/if}
	{/if}
{/snippet}

<DetailSection label="Setup">
	{#snippet headerExtra()}
		<Badge variant={report.complete ? 'success' : 'warning'} size="sm">
			{report.complete ? 'Complete' : setupProgressLabel(report)}
		</Badge>
	{/snippet}

	<div class="space-y-4">
		{#if next}
			<Alert variant="info" icon title="Next: {next.label}">
				{next.description}
				{#snippet actions()}
					{@render actionButton(next, 'primary')}
				{/snippet}
			</Alert>
		{:else if report.complete}
			<Alert variant="success" icon title="Setup complete">Everything this plugin needs is in place.</Alert>
		{/if}

		<ol class="space-y-3" aria-label="Setup steps">
			{#each report.steps as step, index (step.id)}
				<li class="flex items-start gap-3" data-status={step.status}>
					<span
						class="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded border font-mono text-2xs tabular-nums {markerClasses[step.status]}"
						aria-hidden="true"
					>
						{#if step.status === 'done'}
							<Icon name="check" className="h-3 w-3" />
						{:else}
							{index + 1}
						{/if}
					</span>
					<div class="min-w-0 flex-1">
						<p class="text-sm font-medium {step.status === 'todo' ? 'text-fg' : 'text-fg-muted'}">
							{step.label}
							<span class="sr-only">({step.status === 'done' ? 'done' : step.status === 'waiting' ? 'waiting' : 'to do'})</span>
						</p>
						{#if step.description}
							<p class="mt-0.5 text-xs text-fg-subtle">{step.description}</p>
						{/if}
					</div>
					{#if step.status === 'todo' && step.id !== next?.id}
						<div class="flex-shrink-0">
							{@render actionButton(step, 'secondary')}
						</div>
					{/if}
				</li>
			{/each}
		</ol>

		{#if report.guide}
			<div>
				<Button variant="ghost" size="sm" icon="book-open" href={report.guide.href}>Read the {report.guide.title} guide</Button>
			</div>
		{/if}
	</div>
</DetailSection>
