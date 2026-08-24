<script lang="ts">
	import GenerationLayoutPicker from './GenerationLayoutPicker.svelte';
	import AudienceToggle from '$lib/components/AudienceToggle.svelte';
	import type { GenerationLayoutMode } from '$lib/stores/generationLayout';

	// The tabs-row "…" overflow. Hosts the Simple/Advanced field-
	// visibility toggle (moved out of the tabs row's right cluster to keep
	// that down to just the session pill + this menu) and the view-layout
	// picker (relocated out of its own button) - the designated home for
	// anything else the 1a header has no dedicated spot for.
	export let layoutValue: GenerationLayoutMode = 'two';
	export let onLayoutChange: (mode: GenerationLayoutMode) => void;

	let open = false;
	let root: HTMLDivElement;

	function toggle() {
		open = !open;
	}

	function handleWindowClick(event: MouseEvent) {
		if (open && root && !root.contains(event.target as Node)) open = false;
	}
</script>

<svelte:window on:click={handleWindowClick} />

<div class="relative" bind:this={root}>
	<button
		type="button"
		class="inline-flex h-[34px] w-[34px] flex-shrink-0 items-center justify-center rounded border border-line-strong bg-surface-2 text-fg-muted transition-colors hover:border-line-hover hover:bg-surface-3 hover:text-fg"
		on:click={toggle}
		aria-haspopup="menu"
		aria-expanded={open}
		aria-label="More view options"
	>
		<svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
			<circle cx="5" cy="12" r="1.5" />
			<circle cx="12" cy="12" r="1.5" />
			<circle cx="19" cy="12" r="1.5" />
		</svg>
	</button>

	{#if open}
		<div
			class="absolute right-0 top-full z-50 mt-1 w-[min(24rem,calc(100vw-2rem))] rounded-xl border border-line-strong bg-surface-1 p-3 shadow-floating"
			role="menu"
			aria-label="More view options"
		>
			<div class="mb-3 border-b border-line px-1 pb-3">
				<p class="label mb-2">Field visibility</p>
				<AudienceToggle />
			</div>
			<GenerationLayoutPicker
				embedded
				value={layoutValue}
				onChange={(mode) => {
					onLayoutChange(mode);
					open = false;
				}}
			/>
		</div>
	{/if}
</div>
