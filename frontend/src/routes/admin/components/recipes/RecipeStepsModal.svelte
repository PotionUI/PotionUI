<script lang="ts">
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge } from '$lib/components/ui';
	import type { RecipeStepView } from '$lib/services/api/recipes';
	import { stepKindDisplay } from './recipeStepKindDisplay';

	let {
		isOpen,
		steps,
		currentStepKey = null,
		onClose
	}: {
		isOpen: boolean;
		steps: RecipeStepView[];
		currentStepKey?: string | null;
		onClose: () => void;
	} = $props();
</script>

<BaseModal
	{isOpen}
	title="How this recipe works"
	subtitle="{steps.length} step{steps.length === 1 ? '' : 's'}, run in order"
	size="lg"
	on:close={onClose}
>
	<ol class="steps-flow p-4 sm:p-6 flex flex-col gap-3">
		{#each steps as step, index (step.key)}
			{@const display = stepKindDisplay(step.kind)}
			{@const isCurrent = currentStepKey === step.key}
			<li
				class="step-node flex items-start gap-3 rounded-lg border px-3 py-3 {isCurrent
					? 'border-signal bg-signal/5'
					: 'border-line bg-surface-1'}"
				data-recipe-step={step.key}
				data-current={isCurrent}
			>
				<div
					class="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full font-mono text-xs tabular-nums {isCurrent
						? 'bg-signal text-white'
						: 'border border-line-strong bg-surface-2 text-fg-muted'}"
				>
					{index + 1}
				</div>
				<div class="min-w-0 flex-1">
					<div class="flex flex-wrap items-center gap-1.5">
						<Icon name={display.icon} className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
						<p class="text-sm font-medium text-fg truncate">{step.title}</p>
						{#if step.onboarding_only}
							<Badge variant="neutral" size="sm">first run only</Badge>
						{/if}
						{#if isCurrent}
							<Badge variant="signal" size="sm" dot>current</Badge>
						{/if}
					</div>
					<p class="mt-0.5 text-xs text-fg-muted">{display.description}</p>
				</div>
			</li>
		{/each}
	</ol>
</BaseModal>

<style>
	.steps-flow {
		container-type: inline-size;
	}

	.step-node + .step-node {
		position: relative;
	}

	.step-node + .step-node::before {
		content: '';
		position: absolute;
		top: -0.75rem;
		left: 1.5rem;
		width: 1px;
		height: 0.75rem;
		background: rgb(var(--line));
	}

	@container (min-width: 640px) {
		.steps-flow {
			flex-direction: row;
			flex-wrap: wrap;
		}

		.steps-flow > .step-node {
			flex: 1 1 16rem;
		}

		.step-node + .step-node::before {
			display: none;
		}
	}
</style>
