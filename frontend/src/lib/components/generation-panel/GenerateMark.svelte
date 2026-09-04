<script lang="ts">
	import type { MarkState } from './barState';

	// Ported from generation-panel-concept.html's `.generate-button` (lines
	// 254-284, 464-479): the button IS a labeled pill (mark + "Generate"/
	// "Cancel" + shortcut chip), not the old icon-only round button. The mark
	// itself keeps the same anatomy (generation-panel.dc.html's original
	// intent, lines 32/88): no ring around the glyph, colour alone carries
	// ready/running/disabled, and while running the outer wheel (three arcs +
	// their inlet dots) rotates as one ring while the potion + glyph stay put.
	export let state: MarkState;
	export let disabled = false;
	// Long-form accessible name (e.g. "Can't generate yet: choose a preset") —
	// carried as aria-label/title since the mock's visible label is only ever
	// the two-word "Generate"/"Cancel".
	export let label: string;
	// The keybinding hint shown in the shortcut chip (e.g. "⌘↵") — the mock
	// hardcodes "⌘↵"; real functionality it never depicts (a configurable
	// keybinding) gets wired in here instead of being invented separately.
	export let shortcut: string | undefined = undefined;
	export let onclick: (() => void) | undefined = undefined;

	$: isRunning = state === 'running';
</script>

<button
	type="button"
	class="generate-button {isRunning ? 'is-cancel' : ''} {state === 'continuous-armed' ? 'is-continuous-armed' : ''}"
	{disabled}
	on:click={onclick}
	aria-label={label}
	title={label}
>
	<span class="generate-mark" aria-hidden="true">
		<svg viewBox="0 0 48 48" fill="none">
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
	</span>
	<span>{isRunning ? 'Cancel' : 'Generate'}</span>
	{#if shortcut}<span class="shortcut">{shortcut}</span>{/if}
</button>
