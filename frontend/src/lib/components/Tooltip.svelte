<script lang="ts">
	import { onMount, setContext } from 'svelte';
	import { Kbd } from '$lib/components/ui';
	import { INSIDE_TOOLTIP_CONTEXT_KEY } from './tooltipContext';

	// Lets descendants drop their own native `title` fallback rather than
	// rendering a second, unstyled tooltip beside this one.
	setContext(INSIDE_TOOLTIP_CONTEXT_KEY, true);

	export let text: string;
	export let position: 'top' | 'bottom' | 'left' | 'right' = 'top';
	export let delay: number = 200; // ms delay before showing
	// Optional formatted keyboard shortcut (e.g. "Ctrl+K"), rendered as a small
	// mono chip next to the tooltip text. Omit when the action has no binding.
	export let kbd: string | undefined = undefined;
	// Classes for the trigger wrappers. Defaults to inline layout (forms); the
	// sidebar passes a flex/centering class so vertically-stacked icon buttons
	// keep their layout instead of collapsing to inline boxes.
	export let wrapperClass: string = 'inline-flex items-center';

	let showTooltip = false;
	let timeoutId: ReturnType<typeof setTimeout> | null = null;
	let tooltipElement: HTMLDivElement;
	let triggerElement: HTMLDivElement;

	// Position for fixed tooltip
	let tooltipStyle = '';

	function calculatePosition() {
		if (!triggerElement || !tooltipElement) return;

		const triggerRect = triggerElement.getBoundingClientRect();
		const tooltipRect = tooltipElement.getBoundingClientRect();

		let top = 0;
		let left = 0;

		switch (position) {
			case 'top':
				top = triggerRect.top - tooltipRect.height - 8;
				left = triggerRect.left + (triggerRect.width / 2) - (tooltipRect.width / 2);
				break;
			case 'bottom':
				top = triggerRect.bottom + 8;
				left = triggerRect.left + (triggerRect.width / 2) - (tooltipRect.width / 2);
				break;
			case 'left':
				top = triggerRect.top + (triggerRect.height / 2) - (tooltipRect.height / 2);
				left = triggerRect.left - tooltipRect.width - 8;
				break;
			case 'right':
				top = triggerRect.top + (triggerRect.height / 2) - (tooltipRect.height / 2);
				left = triggerRect.right + 8;
				break;
		}

		// Keep tooltip within viewport
		const padding = 8;
		if (left < padding) left = padding;
		if (left + tooltipRect.width > window.innerWidth - padding) {
			left = window.innerWidth - tooltipRect.width - padding;
		}
		if (top < padding) top = padding;
		if (top + tooltipRect.height > window.innerHeight - padding) {
			top = window.innerHeight - tooltipRect.height - padding;
		}

		tooltipStyle = `top: ${top}px; left: ${left}px;`;
	}

	function handleMouseEnter() {
		timeoutId = setTimeout(() => {
			showTooltip = true;
			// Wait for next tick to calculate position after tooltip is rendered
			requestAnimationFrame(() => {
				calculatePosition();
			});
		}, delay);
	}

	function handleMouseLeave() {
		if (timeoutId) {
			clearTimeout(timeoutId);
			timeoutId = null;
		}
		showTooltip = false;
	}

	onMount(() => {
		return () => {
			if (timeoutId) {
				clearTimeout(timeoutId);
			}
		};
	});
</script>

<div class={wrapperClass}>
	<!-- Trigger element -->
	<div
		bind:this={triggerElement}
		on:mouseenter={handleMouseEnter}
		on:mouseleave={handleMouseLeave}
		class={wrapperClass}
	>
		<slot />
	</div>
</div>

<!-- Tooltip rendered fixed to escape overflow containers -->
{#if showTooltip && text}
	<div
		bind:this={tooltipElement}
		aria-hidden="true"
		style="min-width: max-content; max-width: 300px; {tooltipStyle}"
		class="fixed z-[9999] px-2 py-1 text-xs font-medium text-fg bg-surface-3 rounded-md shadow-lg pointer-events-none animate-in fade-in duration-150 break-words flex items-center gap-1.5"
	>
		<span>{text}</span>
		{#if kbd}
			<Kbd keys={kbd} />
		{/if}
		<!-- Arrow -->
		<div
			class="absolute w-2 h-2 bg-surface-3 rotate-45
				{position === 'top'
				? 'bottom-[-4px] left-1/2 -translate-x-1/2'
				: position === 'bottom'
					? 'top-[-4px] left-1/2 -translate-x-1/2'
					: position === 'left'
						? 'right-[-4px] top-1/2 -translate-y-1/2'
						: 'left-[-4px] top-1/2 -translate-y-1/2'}"
		/>
	</div>
{/if}

<style>
	@keyframes fade-in {
		from {
			opacity: 0;
		}
		to {
			opacity: 1;
		}
	}

	.animate-in {
		animation: fade-in 150ms ease-out;
	}
</style>