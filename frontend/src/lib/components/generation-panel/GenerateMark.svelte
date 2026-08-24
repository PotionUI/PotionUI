<script lang="ts">
	import type { MarkState } from './barState';

	// The Inlets mark IS the button (generation-panel.dc.html lines 32, 88):
	// no pill around it, colour alone carries ready/running/disabled, and while
	// running the outer wheel (three arcs + their inlet dots) rotates together
	// as one ring while the potion + glyph stay put.
	export let state: MarkState;
	export let disabled = false;
	export let label: string;
	export let onclick: (() => void) | undefined = undefined;

	$: isRunning = state === 'running';
	$: colorClass =
		state === 'running'
			? 'text-danger hover:text-danger/80 hover:bg-danger/[0.08]'
			: state === 'disabled'
				? 'text-fg-disabled cursor-not-allowed'
				: 'text-signal hover:bg-signal/[0.08]';
	$: ringAnimationClass =
		state === 'running' ? 'om-mark-orbit-fast' : state === 'continuous-armed' ? 'om-mark-orbit-slow' : '';
</script>

<button
	type="button"
	class="relative inline-flex h-14 w-14 flex-shrink-0 items-center justify-center rounded-full transition-colors active:scale-95 disabled:pointer-events-none {colorClass}"
	{disabled}
	on:click={onclick}
	aria-label={label}
	title={label}
>
	<svg class="h-12 w-12" viewBox="0 0 48 48" fill="none" aria-hidden="true">
		{#if isRunning}
			<path
				fill="currentColor"
				fill-rule="evenodd"
				d="M24 15.5a8.5 8.5 0 100 17 8.5 8.5 0 000-17zM24 26.21 20.51 29.70 18.30 27.49 21.79 24 18.30 20.51 20.51 18.30 24 21.79 27.49 18.30 29.70 20.51 26.21 24 29.70 27.49 27.49 29.70z"
			/>
		{:else}
			<path
				fill="currentColor"
				fill-rule="evenodd"
				d="M24 15.5a8.5 8.5 0 100 17 8.5 8.5 0 000-17zm-3.4 3.1 9.4 5.4-9.4 5.4z"
			/>
		{/if}
		<g class={ringAnimationClass} style="transform-box: view-box; transform-origin: 24px 24px;">
			<g fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
				<path d="M27.51 9.93 A14.5 14.5 0 0 1 37.94 28.0" />
				<path d="M34.43 34.07 A14.5 14.5 0 0 1 13.57 34.07" />
				<path d="M10.06 28.0 A14.5 14.5 0 0 1 20.49 9.93" />
			</g>
			<g fill="currentColor">
				<circle cx="24" cy="6" r="1.7" />
				<circle cx="39.6" cy="33" r="1.7" />
				<circle cx="8.4" cy="33" r="1.7" />
			</g>
		</g>
	</svg>
</button>

<style>
	.om-mark-orbit-fast {
		animation: om-mark-orbit 3.2s linear infinite;
	}
	.om-mark-orbit-slow {
		animation: om-mark-orbit 14s linear infinite;
	}
	@keyframes om-mark-orbit {
		to {
			transform: rotate(360deg);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.om-mark-orbit-fast,
		.om-mark-orbit-slow {
			animation: none;
		}
	}
</style>
