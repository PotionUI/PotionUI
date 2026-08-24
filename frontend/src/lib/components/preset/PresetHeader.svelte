<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { PresetInfo, PresetModeVariant } from '$lib/services/api/index';
	import type { ReadinessReport } from '$lib/services/api/setup';
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import PresetPicker from '$lib/components/preset/PresetPicker.svelte';
	import PresetMediaModal from '$lib/components/preset/PresetMediaModal.svelte';
	import { hasPresetMedia } from '$lib/utils/presetMedia';
	import { resolveVariant, sortVariants } from '$lib/utils/variants';

	// The preset card + mode/variant selectors, extracted from the old
	// PresetSessionBar so they can mount at the top of the settings pane
	// (above DynamicForm) instead of a full-width bar above the tabs. Session
	// state lives in SessionPill now; this component only knows about presets.
	export let presets: PresetInfo[] = [];
	export let selectedPreset: string = '';
	export let isLoading: boolean = false;
	export let isReloading: boolean = false;
	// Forwarded straight through to PresetPicker - see its own doc comment.
	export let readiness: ReadinessReport | null = null;
	export let selectedMode: string = '';
	export let availableModes: Array<{
		id: string;
		label: string;
		variants?: PresetModeVariant[];
		/** The contributing plugin's id when this mode came from a plugin's
		 *  `preset_modes:` - a small provenance hint, not a state. */
		sourcePlugin?: string | null;
	}> = [
		{ id: 'txt2img', label: 'Text to Image' },
		{ id: 'img2img', label: 'Image to Image' },
		{ id: 'inpaint', label: 'Inpainting' }
	];
	export let selectedVariant: string | null = null;

	const dispatch = createEventDispatcher<{
		presetChange: string;
		modeChange: string;
		variantChange: string;
		reload: void;
	}>();

	let isDescriptionModalOpen = false;

	$: safePresets = Array.isArray(presets) ? presets : [];
	$: selectedPresetObj = safePresets.find((p) => p.id === selectedPreset);
	$: currentModeVariants = sortVariants(
		availableModes.find((mode) => mode.id === selectedMode)?.variants
	);
	$: variantOptions = currentModeVariants.map((v) => ({
		value: v.name,
		label: v.label,
		description: v.description
	}));
	$: showCardActions = selectedPreset || (selectedPresetObj && (hasPresetMedia(selectedPresetObj) || selectedPresetObj.description));

	function handlePresetSelect(presetId: string) {
		dispatch('presetChange', presetId);
	}

	function handleModeChange(modeId: string) {
		dispatch('modeChange', modeId);
	}

	function handleVariantChange(newValue: string) {
		if (!newValue || newValue === selectedVariant) return;
		dispatch('variantChange', newValue);
	}

	function handleReload() {
		dispatch('reload');
	}

	function openDescriptionModal() {
		isDescriptionModalOpen = true;
	}

	function closeDescriptionModal() {
		isDescriptionModalOpen = false;
	}
</script>

<div class="flex flex-col gap-2">
	<div class="flex items-stretch gap-1.5">
		<div class="min-w-0 flex-1">
			<PresetPicker
				{presets}
				{selectedPreset}
				{readiness}
				loading={isLoading}
				on:select={(event) => handlePresetSelect(event.detail)}
			/>
		</div>

		{#if showCardActions}
			<div class="flex flex-shrink-0 flex-col gap-1">
				{#if selectedPresetObj && (hasPresetMedia(selectedPresetObj) || selectedPresetObj.description)}
					<Tooltip text="View preset description and examples" position="left" delay={150}>
						<button
							type="button"
							class="flex h-[26px] w-[26px] items-center justify-center rounded border border-line-strong bg-surface-2 text-fg-muted transition-colors hover:border-line-hover hover:bg-surface-3 hover:text-fg"
							on:click={openDescriptionModal}
							aria-label="View preset description and examples"
						>
							<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
								/>
							</svg>
						</button>
					</Tooltip>
				{/if}

				{#if selectedPreset}
					<Tooltip text={isReloading ? 'Reloading preset' : 'Reload preset from disk'} position="left" delay={150}>
						<button
							type="button"
							class="flex h-[26px] w-[26px] items-center justify-center rounded border border-line-strong bg-surface-2 text-fg-muted transition-colors hover:border-line-hover hover:bg-surface-3 hover:text-fg disabled:opacity-50"
							on:click={handleReload}
							disabled={isReloading}
							aria-label="Reload preset from disk"
						>
							<svg
								class="w-3 h-3 {isReloading ? 'animate-spin' : ''}"
								fill="none"
								stroke="currentColor"
								viewBox="0 0 24 24"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
								/>
							</svg>
						</button>
					</Tooltip>
				{/if}
			</div>
		{/if}
	</div>

	{#if availableModes.length > 0}
		<div
			class="grid gap-0.5 rounded-lg bg-surface-2 p-0.5"
			style="grid-template-columns: repeat({availableModes.length}, minmax(0, 1fr));"
		>
			{#each availableModes as mode}
				{#if mode.sourcePlugin}
					<!-- wrapperClass="flex w-full" on BOTH of Tooltip's nested wrapper divs:
						the outer one is the grid item (stretches to its column on its own),
						but the inner one is a flex CHILD of that outer div and defaults to
						content-width - without an explicit w-full here it never actually
						fills the column, leaving this segment visibly narrower than its
						siblings (the bug this replaces). -->
					<Tooltip
						text={`${mode.label} — contributed by ${mode.sourcePlugin}`}
						position="bottom"
						delay={150}
						wrapperClass="flex w-full"
					>
						<button
							type="button"
							class="flex w-full min-w-0 items-center justify-center gap-1 rounded-md px-3 py-1.5 text-xs font-medium transition-all {selectedMode === mode.id
								? 'bg-signal/10 text-signal shadow-sm'
								: 'text-fg-muted hover:text-fg hover:bg-surface-3/50'}"
							on:click={() => handleModeChange(mode.id)}
						>
							<span class="min-w-0 truncate">{mode.label}</span>
							<span class="flex-shrink-0 font-mono text-2xs text-fg-subtle" aria-hidden="true">&bull;</span>
						</button>
					</Tooltip>
				{:else}
					<button
						type="button"
						class="w-full min-w-0 rounded-md px-3 py-1.5 text-xs font-medium transition-all {selectedMode === mode.id
							? 'bg-signal/10 text-signal shadow-sm'
							: 'text-fg-muted hover:text-fg hover:bg-surface-3/50'}"
						title={mode.label}
						on:click={() => handleModeChange(mode.id)}
					>
						<span class="block truncate">{mode.label}</span>
					</button>
				{/if}
			{/each}
		</div>
	{/if}

	{#if variantOptions.length > 1}
		{#if variantOptions.length <= 3}
			<div
				class="grid gap-0.5 rounded-lg bg-surface-2 p-0.5"
				style="grid-template-columns: repeat({variantOptions.length}, minmax(0, 1fr));"
			>
				{#each variantOptions as opt}
					{#if opt.description}
						<Tooltip text={opt.description} position="bottom" delay={150} wrapperClass="flex w-full">
							<button
								type="button"
								class="w-full min-w-0 rounded-md px-3 py-1.5 text-xs font-medium transition-all {selectedVariant === opt.value
									? 'bg-signal/10 text-signal shadow-sm'
									: 'text-fg-muted hover:text-fg hover:bg-surface-3/50'}"
								on:click={() => handleVariantChange(opt.value)}
							>
								<span class="block truncate">{opt.label}</span>
							</button>
						</Tooltip>
					{:else}
						<button
							type="button"
							class="w-full min-w-0 rounded-md px-3 py-1.5 text-xs font-medium transition-all {selectedVariant === opt.value
								? 'bg-signal/10 text-signal shadow-sm'
								: 'text-fg-muted hover:text-fg hover:bg-surface-3/50'}"
							title={opt.label}
							on:click={() => handleVariantChange(opt.value)}
						>
							<span class="block truncate">{opt.label}</span>
						</button>
					{/if}
				{/each}
			</div>
		{:else}
			<CustomSelect
				value={selectedVariant}
				options={variantOptions}
				size="sm"
				placeholder="Select variant..."
				on:change={(e) => handleVariantChange(e.detail)}
			/>
		{/if}
	{/if}
</div>

<PresetMediaModal
	isOpen={isDescriptionModalOpen}
	preset={selectedPresetObj ?? null}
	on:close={closeDescriptionModal}
/>
