<script lang="ts">
	import StudioSheet from '$lib/components/StudioSheet.svelte';
	import PromptSection from '../PromptSection.svelte';
	import type { GenerationTab } from '$lib/types/tabs';
	import type { DirectorCapabilities } from '$lib/types/videoDirector';
	import type { MusicDirectorCapabilities } from '$lib/types/musicDirector';
	import type { RegionalCaps } from '$lib/regional/regions';
	import type { PresetSegmentTemplate } from '$lib/utils/presetSegmentTemplates';
	import { NO_PROMPT_COPY, type PromptCopy } from '$lib/utils/promptModeCopy';
	import type { PresetStyle } from '$lib/services/api/index';

	export let tab: GenerationTab;
	export let tabHandlers: {
		handlePromptChange: (prompt: string) => void;
		handlePromptSegmentsChange: (segments: any[]) => void;
		handleNegativePromptChange: (prompt: string) => void;
		handleNegativePromptSegmentsChange: (segments: any[]) => void;
	};
	export let promptRelayActive: boolean;
	export let regionalCaps: RegionalCaps | null = null;
	export let videoDirectorActive: boolean;
	export let videoDirectorCaps: DirectorCapabilities | null;
	export let musicDirectorActive: boolean;
	export let musicDirectorCaps: MusicDirectorCapabilities | null;
	export let canBatch: boolean;
	export let negativePromptSupported: boolean;
	export let negativeInert: boolean;
	export let presetSegmentTemplates: PresetSegmentTemplate[] = [];
	export let promptCopy: PromptCopy = NO_PROMPT_COPY;
	export let presetStyles: PresetStyle[] = [];
	export let onClose: () => void;

	$: segmentCount = tab.promptSegments?.length ?? 0;
</script>

<StudioSheet maxHeight="88%" ariaLabel="Prompt" on:close={onClose}>
	<svelte:fragment slot="header">
		<div class="flex items-baseline gap-2">
			<span class="text-sm font-semibold text-fg">Prompt</span>
			<span class="font-mono text-sm text-fg-subtle"
				>{segmentCount} {segmentCount === 1 ? 'SEGMENT' : 'SEGMENTS'}</span
			>
		</div>
	</svelte:fragment>

	<div class="pb-4">
		<PromptSection
			{tab}
			{tabHandlers}
			{promptRelayActive}
			{regionalCaps}
			{videoDirectorActive}
			{videoDirectorCaps}
			{musicDirectorActive}
			{musicDirectorCaps}
			{presetSegmentTemplates}
			{promptCopy}
			{presetStyles}
			{canBatch}
			{negativePromptSupported}
			{negativeInert}
			spacingClass="mt-0"
		/>
	</div>

	<svelte:fragment slot="footer">
		<button
			type="button"
			class="h-11 w-full rounded-lg bg-accent text-sm font-semibold text-accent-contrast"
			on:click={onClose}
		>
			Done
		</button>
	</svelte:fragment>
</StudioSheet>
