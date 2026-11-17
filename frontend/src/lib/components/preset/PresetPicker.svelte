<script module lang="ts">
	export const PRESET_PLACEHOLDER = 'Choose a preset';
</script>

<script lang="ts">
	import { createEventDispatcher, type Snippet } from 'svelte';
	import type { PresetInfo } from '$lib/types/api';
	import type { ReadinessReport } from '$lib/services/api/setup';
	import PresetThumbnail from './PresetThumbnail.svelte';
	import PresetDropdown from './PresetDropdown.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Spinner } from '$lib/components/ui';
	import { isMobile } from '$lib/stores/viewport';

	export let presets: PresetInfo[] = [];
	export let selectedPreset: string = '';
	export let loading = false;
	export let isReloading = false;
	export let disabled = false;
	export let readiness: ReadinessReport | null = null;
	export let disabledReasons: Record<string, string> = {};
	export let recordRecent = true;
	export let trigger:
		| Snippet<
				[
					{
						openPicker: (event?: Event) => void;
						currentPreset: PresetInfo | null;
						loading: boolean;
						disabled: boolean;
						expanded: boolean;
					}
				]
		  >
		| undefined = undefined;

	const dispatch = createEventDispatcher<{ select: string; reload: void }>();

	let open = false;
	let anchor: HTMLElement | undefined;
	let fallbackAnchor: HTMLElement | undefined;

	$: safePresets = Array.isArray(presets) ? presets : [];
	$: currentPreset = safePresets.find((preset) => preset.id === selectedPreset) ?? null;

	function openPicker(event?: Event) {
		if (disabled || loading) return;
		if (open) {
			open = false;
			return;
		}
		anchor = (event?.currentTarget as HTMLElement | null) ?? fallbackAnchor;
		open = true;
	}

	function closePicker() {
		open = false;
	}

	export function openAt(element: HTMLElement | undefined) {
		if (disabled || loading || open) return;
		anchor = element ?? fallbackAnchor;
		open = true;
	}
</script>

{#if trigger}
	{@render trigger({ openPicker, currentPreset, loading, disabled: disabled || loading, expanded: open })}
{:else}
	<button
		type="button"
		class="w-full h-full min-w-0 flex items-center gap-2 px-3 py-2 text-left bg-surface-1 border border-line-strong rounded hover:bg-surface-2 hover:border-line-hover focus-visible:ring-1 focus-visible:ring-signal/50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
		bind:this={fallbackAnchor}
		on:click={openPicker}
		aria-expanded={open}
		disabled={disabled || loading}
		aria-haspopup="dialog"
		class:preset-picker-cta={!currentPreset && !loading}
		data-testid="preset-picker-trigger"
	>
		{#if loading}
			<Spinner size="sm" />
		{:else if currentPreset}
			<PresetThumbnail
				presetId={currentPreset.id}
				presetName={currentPreset.name}
				cover={currentPreset.media?.cover}
				category={currentPreset.category}
				size="w-6 h-6"
				zoom={false}
			/>
		{/if}
		<span class="min-w-0 flex-1 truncate text-sm font-semibold text-fg">{currentPreset?.name || PRESET_PLACEHOLDER}</span>
		<Icon name="chevron-down" className="w-4 h-4 text-fg-subtle flex-shrink-0 transition-transform {open ? 'rotate-180' : ''}" />
	</button>
{/if}

{#if open}
	<PresetDropdown
		presets={safePresets}
		{selectedPreset}
		{readiness}
		{isReloading}
		{anchor}
		fullscreen={$isMobile}
		{disabledReasons}
		{recordRecent}
		onSelect={(presetId) => dispatch('select', presetId)}
		onReload={() => dispatch('reload')}
		onClose={closePicker}
	/>
{/if}

<style>
	@property --preset-cta-angle {
		syntax: '<angle>';
		inherits: false;
		initial-value: 0deg;
	}

	:global(.preset-picker-cta) {
		border-color: transparent;
		background:
			linear-gradient(rgb(var(--surface-1)), rgb(var(--surface-1))) padding-box,
			conic-gradient(
					from var(--preset-cta-angle),
					rgb(var(--accent) / 0.35) 0deg,
					rgb(var(--accent)) 70deg,
					rgb(var(--accent) / 0.35) 140deg,
					rgb(var(--accent) / 0.35) 180deg,
					rgb(var(--accent)) 250deg,
					rgb(var(--accent) / 0.35) 320deg,
					rgb(var(--accent) / 0.35) 360deg
				)
				border-box;
		animation:
			preset-cta-sweep 2.4s linear infinite,
			preset-cta-glow 2.4s ease-in-out infinite;
	}

	:global(.preset-picker-cta:hover) {
		background:
			linear-gradient(rgb(var(--surface-2)), rgb(var(--surface-2))) padding-box,
			linear-gradient(rgb(var(--accent)), rgb(var(--accent))) border-box;
		animation: none;
	}

	@keyframes preset-cta-sweep {
		to {
			--preset-cta-angle: 360deg;
		}
	}

	@keyframes preset-cta-glow {
		0%,
		100% {
			box-shadow: 0 0 0 0 rgb(var(--accent) / 0);
		}
		50% {
			box-shadow: 0 0 0 4px rgb(var(--accent) / 0.12);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		:global(.preset-picker-cta) {
			animation: none;
			background:
				linear-gradient(rgb(var(--surface-1)), rgb(var(--surface-1))) padding-box,
				linear-gradient(rgb(var(--accent) / 0.7), rgb(var(--accent) / 0.7)) border-box;
		}
	}
</style>
