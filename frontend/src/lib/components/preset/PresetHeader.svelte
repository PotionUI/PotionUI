<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import { createEventDispatcher } from 'svelte';
	import type { PresetInfo, PresetModeVariant } from '$lib/services/api/index';
	import type { ReadinessReport } from '$lib/services/api/setup';
	import PickerSelect from '$lib/components/preset/PickerSelect.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import PresetPicker from '$lib/components/preset/PresetPicker.svelte';
	import { resolveVariant, sortVariants } from '$lib/utils/variants';
	import { presetModeOptions } from '$lib/utils/presetModeOptions';
	import { authStore } from '$lib/stores/auth';
	import type { PresetModeInstall } from '$lib/types/api';

	export let presets: PresetInfo[] = [];
	export let selectedPreset: string = '';
	export let isLoading: boolean = false;
	export let isReloading: boolean = false;
	export let readiness: ReadinessReport | null = null;
	export let selectedMode: string = '';
	export let availableModes: Array<{
		id: string;
		label: string;
		variants?: PresetModeVariant[];
		sourcePlugin?: string | null;
		description?: string | null;
		icon?: string | null;
		requirementsMissing?: string[] | null;
		install?: PresetModeInstall | null;
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

	let panelWidth = 0;

	const NARROW_PANEL_PX = 340;
	$: narrow = panelWidth > 0 && panelWidth < NARROW_PANEL_PX;
	$: hasModeSelect = availableModes.length > 1;
	$: modeOptions = presetModeOptions(availableModes, { admin: $authStore.user?.account_type === 'ADMIN' });

	$: currentModeVariants = sortVariants(
		availableModes.find((mode) => mode.id === selectedMode)?.variants
	);
	$: variantOptions = currentModeVariants.map((v) => ({
		value: v.name,
		label: v.label,
		description: v.description
	}));

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
</script>

<div class="flex flex-col gap-2" bind:clientWidth={panelWidth}>
	<div class="flex flex-wrap items-stretch gap-1.5" data-testid="preset-header-row">
		<div class="min-w-0 flex-1" data-testid="preset-header-picker">
			<PresetPicker
				{presets}
				{selectedPreset}
				{readiness}
				loading={isLoading}
				{isReloading}
				on:reload={handleReload}
				on:select={(event) => handlePresetSelect(event.detail)}
			/>
		</div>

		{#if hasModeSelect}
			<div
				class="flex {narrow ? 'order-3 basis-full' : 'w-[168px] flex-shrink-0'}"
				data-testid="preset-header-mode"
			>
				<PickerSelect
					value={selectedMode}
					options={modeOptions}
					label="Choose mode"
					placeholder="Mode"
					width={400}
					triggerDescription={narrow}
					onchange={handleModeChange}
				/>
			</div>
		{/if}
	</div>

	{#if variantOptions.length > 1}
		{#if variantOptions.length <= 3}
			<div
				class="grid gap-0.5 rounded-lg bg-surface-2 p-0.5"
				role="group"
				aria-label="Preset variant"
				data-testid="preset-variant-switch"
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
							use:tooltip={opt.label}
							on:click={() => handleVariantChange(opt.value)}
						>
							<span class="block truncate">{opt.label}</span>
						</button>
					{/if}
				{/each}
			</div>
		{:else}
			<div class="flex">
				<PickerSelect
					value={selectedVariant}
					options={variantOptions}
					label="Choose variant"
					placeholder="Select variant..."
					onchange={handleVariantChange}
				/>
			</div>
		{/if}
	{/if}
</div>
