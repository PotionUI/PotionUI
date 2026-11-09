<script lang="ts">
	import { onMount, setContext } from 'svelte';
	import { INSIDE_TOOLTIP_CONTEXT_KEY } from './tooltipContext';
	import TooltipBubble from './TooltipBubble.svelte';
	import { isOverflowing } from '$lib/actions/tooltip';
	import { watchTooltipTrigger } from '$lib/actions/tooltipTrigger';

	setContext(INSIDE_TOOLTIP_CONTEXT_KEY, true);

	export let text: string;
	export let position: 'top' | 'bottom' | 'left' | 'right' = 'top';
	export let delay: number = 200;
	export let kbd: string | string[] | undefined = undefined;
	export let wrapperClass: string = 'inline-flex items-center';
	export let truncateOnly: boolean = false;

	const id = `tooltip-${Math.random().toString(36).slice(2, 10)}`;
	let showTooltip = false;
	let triggerElement: HTMLDivElement;
	let describedElement: Element | null = null;

	function reveal() {
		if (truncateOnly && !isOverflowing(triggerElement)) return;
		showTooltip = true;
		describedElement = triggerElement.firstElementChild;
		describedElement?.setAttribute('aria-describedby', id);
	}

	function hide() {
		showTooltip = false;
		describedElement?.removeAttribute('aria-describedby');
		describedElement = null;
	}

	onMount(() =>
		watchTooltipTrigger(triggerElement, {
			delay: () => delay,
			open: reveal,
			close: hide
		})
	);
</script>

<div class={wrapperClass}>
	<div
		bind:this={triggerElement}
		role="presentation"
		class={wrapperClass}
	>
		<slot />
	</div>
</div>

{#if showTooltip && text}
	<TooltipBubble {text} {id} {position} {kbd} anchor={triggerElement} />
{/if}
