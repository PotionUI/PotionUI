<script lang="ts">
	import {
		SETUP_STEPS,
		SETUP_STEP_COUNT,
		canNavigateToStep,
		stepIndex,
		stepLabel,
		type SetupStepId
	} from '$lib/utils/setupGuide';

	let {
		current,
		ownerExists,
		onnavigate
	}: {
		current: SetupStepId;
		ownerExists: boolean;
		onnavigate?: (id: SetupStepId) => void;
	} = $props();

	const currentIndex = $derived(stepIndex(current));
</script>

<nav class="border-b border-line bg-canvas" aria-label="Setup progress">
	<ol class="mx-auto hidden max-w-[960px] items-center px-6 sm:flex">
		{#each SETUP_STEPS as step, i (step.id)}
			{@const done = i < currentIndex}
			{@const isCurrent = i === currentIndex}
			{@const clickable = done && !!onnavigate && canNavigateToStep(step.id, current, ownerExists)}
			<li class="flex min-w-0 items-center {i < SETUP_STEP_COUNT - 1 ? 'flex-1' : ''}">
				<button
					type="button"
					class="flex items-center gap-2 whitespace-nowrap rounded py-3.5 {isCurrent
						? 'text-fg'
						: done
							? 'text-fg-muted'
							: 'text-fg-subtle'} {clickable ? 'cursor-pointer hover:text-fg' : 'cursor-default'}"
					disabled={!clickable}
					aria-current={isCurrent ? 'step' : undefined}
					aria-label={clickable ? `Back to ${step.label}` : undefined}
					onclick={() => onnavigate?.(step.id)}
				>
					<span
						class="grid h-[22px] w-[22px] flex-none place-items-center rounded border font-mono text-sm {isCurrent
							? 'border-signal bg-signal/10 text-signal'
							: done
								? 'border-success/50 text-success'
								: 'border-line-strong'}"
					>
						{done ? '✓' : i + 1}
					</span>
					<span class="font-medium">{step.label}</span>
				</button>
				{#if i < SETUP_STEP_COUNT - 1}
					<span
						class="mx-2.5 h-px min-w-3 flex-1 {done ? 'bg-fg-subtle' : 'bg-line-strong'}"
						aria-hidden="true"
					></span>
				{/if}
			</li>
		{/each}
	</ol>

	<div class="flex flex-col gap-2 px-4 pb-3.5 pt-3 sm:hidden">
		<div class="flex items-baseline justify-between gap-2 font-mono text-sm uppercase tracking-[0.06em]">
			<span class="text-fg-subtle">Step {currentIndex + 1} of {SETUP_STEP_COUNT}</span>
			<span class="text-fg">{stepLabel(current)}</span>
		</div>
		<div
			class="grid gap-[3px]"
			style="grid-template-columns: repeat({SETUP_STEP_COUNT}, minmax(0, 1fr))"
			aria-hidden="true"
		>
			{#each SETUP_STEPS as step, i (step.id)}
				<i
					class="block h-1 rounded-sm {i === currentIndex
						? 'bg-signal'
						: i < currentIndex
							? 'bg-fg-subtle'
							: 'bg-line-strong'}"
				></i>
			{/each}
		</div>
	</div>
</nav>
