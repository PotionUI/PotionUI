<script lang="ts">
	import { Kbd } from '$lib/components/ui';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';

	type Position = 'top' | 'bottom' | 'left' | 'right';

	let {
		text,
		anchor,
		position = 'top',
		kbd,
		wrap = false,
		id
	}: {
		text: string;
		anchor: HTMLElement;
		position?: Position;
		kbd?: string | string[];
		wrap?: boolean;
		id?: string;
	} = $props();

	let kbdLabel = $derived(Array.isArray(kbd) ? kbd.join('+') : kbd);

	let bubble: HTMLDivElement | undefined = $state();
	let style = $state('');

	$effect(() => {
		if (!bubble) return;
		const a = anchor.getBoundingClientRect();
		const b = bubble.getBoundingClientRect();
		let top = 0;
		let left = 0;
		if (position === 'top' || position === 'bottom') {
			top = position === 'top' ? a.top - b.height - 8 : a.bottom + 8;
			left = a.left + a.width / 2 - b.width / 2;
		} else {
			top = a.top + a.height / 2 - b.height / 2;
			left = position === 'left' ? a.left - b.width - 8 : a.right + 8;
		}
		const padding = 8;
		left = Math.max(padding, Math.min(left, window.innerWidth - b.width - padding));
		top = Math.max(padding, Math.min(top, window.innerHeight - b.height - padding));
		style = `top: ${top}px; left: ${left}px;`;
	});

	const arrowClass: Record<Position, string> = {
		top: 'bottom-[-4px] left-1/2 -translate-x-1/2',
		bottom: 'top-[-4px] left-1/2 -translate-x-1/2',
		left: 'right-[-4px] top-1/2 -translate-y-1/2',
		right: 'left-[-4px] top-1/2 -translate-y-1/2'
	};
</script>

<div
	bind:this={bubble}
	{id}
	role="tooltip"
	use:portal
	use:overlayLayer={'tooltip'}
	style="{wrap ? 'max-width: min(360px, calc(100vw - 24px));' : 'min-width: max-content; max-width: 300px;'} {style}"
	class="fixed z-tooltip px-2 py-1 text-sm font-medium text-fg bg-surface-3 rounded-md shadow-lg pointer-events-none animate-in fade-in duration-150 break-words flex items-center gap-1.5"
>
	<span>{text}</span>
	{#if kbd}
		<Kbd keys={kbd} />
		<span class="sr-only">{kbdLabel}</span>
	{/if}
	<div class="absolute w-2 h-2 bg-surface-3 rotate-45 {arrowClass[position]}"></div>
</div>

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
