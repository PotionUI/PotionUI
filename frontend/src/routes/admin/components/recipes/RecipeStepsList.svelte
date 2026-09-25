<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Badge } from '$lib/components/ui';
	import { DETAIL_INSET_CLASS } from '$lib/components/detail';
	import type { RecipeStepView } from '$lib/services/api/recipes';
	import { stepKindDisplay } from './recipeStepKindDisplay';

	let { steps }: { steps: RecipeStepView[] } = $props();
</script>

<ol class="flex flex-col gap-2">
	{#each steps as step, index (step.key)}
		{@const display = stepKindDisplay(step.kind)}
		<li
			class="step-node flex items-center gap-3 px-3 py-2.5 {DETAIL_INSET_CLASS}"
			data-recipe-step={step.key}
		>
			<div
				class="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full border border-line-strong bg-surface-2 font-mono text-xs tabular-nums text-fg-muted"
			>
				{index + 1}
			</div>
			<Icon name={display.icon} className="h-4 w-4 flex-shrink-0 text-fg-subtle" />
			<div class="min-w-0 flex-1 sm:flex sm:items-baseline sm:gap-3">
				<p class="text-sm font-medium text-fg sm:shrink-0">{step.title}</p>
				<p class="text-sm text-fg-muted">{display.description}</p>
			</div>
			{#if step.onboarding_only}
				<Badge variant="neutral" size="sm">first run only</Badge>
			{/if}
		</li>
	{/each}
</ol>

<style>
	.step-node + .step-node {
		position: relative;
	}

	.step-node + .step-node::before {
		content: '';
		position: absolute;
		top: -0.5rem;
		left: calc(0.75rem + 1rem);
		width: 1px;
		height: 0.5rem;
		background: rgb(var(--line));
	}
</style>
