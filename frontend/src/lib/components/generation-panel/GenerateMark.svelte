<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import { getContext } from 'svelte';
	import { INSIDE_TOOLTIP_CONTEXT_KEY } from '../tooltipContext';
	import type { MarkState } from './barState';

	const insideTooltip = getContext<boolean | undefined>(INSIDE_TOOLTIP_CONTEXT_KEY) === true;

	export let state: MarkState;
	export let disabled = false;
	export let label: string;
	export let onclick: (() => void) | undefined = undefined;

	$: isRunning = state === 'running';
</script>

<button
	type="button"
	class="generate-button {isRunning ? 'is-cancel' : ''}"
	{disabled}
	on:click={onclick}
	aria-label={label}
	use:tooltip={insideTooltip ? undefined : label}
>
	<svg class="generate-mark" viewBox="0 0 48 48" fill="none" aria-hidden="true">
		{#if isRunning}
			<path
				fill="currentColor"
				fill-rule="evenodd"
				d="M24 15.5a8.5 8.5 0 100 17 8.5 8.5 0 000-17zM24 26.21 20.51 29.70 18.30 27.49 21.79 24 18.30 20.51 20.51 18.30 24 21.79 27.49 18.30 29.70 20.51 26.21 24 29.70 27.49 27.49 29.70z"
			/>
		{:else}
			<path fill="currentColor" fill-rule="evenodd" d="M24 15.5a8.5 8.5 0 100 17 8.5 8.5 0 000-17zm-3.4 3.1 9.4 5.4-9.4 5.4z" />
		{/if}
		<g class="mark-orbit">
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
