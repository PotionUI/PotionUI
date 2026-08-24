<script lang="ts">
	import Logo from './Logo.svelte';

	/** Size of the mark in px; the wordmark scales with it. */
	export let size = 24;
	/** Stack the mark above the wordmark (centered) instead of placing them inline. */
	export let stacked = false;
	/** Optional accessible name for the whole lockup. */
	export let label: string | undefined = undefined;

	// Wordmark "Potion" is sized relative to the mark; "UI" is ~0.82x of that.
	$: wordFontSize = size * 0.6;
	$: gap = stacked ? size * 0.18 : size * 0.34;
</script>

<span
	class="inline-flex items-center leading-none text-fg"
	class:flex-col={stacked}
	style="gap: {gap}px"
	role={label ? 'img' : undefined}
	aria-label={label}
>
	<Logo {size} />
	<span
		class="wordmark leading-none"
		style="font-size: {wordFontSize}px"
		aria-hidden={label ? true : undefined}
	>
		<span class="potion">Potion</span><span class="ui">UI</span>
	</span>
</span>

<style>
	.wordmark {
		white-space: nowrap;
	}

	.potion {
		font-family: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui,
			sans-serif;
		font-weight: 600;
		letter-spacing: -0.015em;
	}

	.ui {
		font-family: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
		font-weight: 500;
		font-size: 0.82em;
		letter-spacing: 0.12em;
		margin-left: 0.09em;
	}
</style>
