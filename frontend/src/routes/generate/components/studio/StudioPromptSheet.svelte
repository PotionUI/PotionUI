<script lang="ts">
	import StudioSheet from './StudioSheet.svelte';
	import PromptSection from '../PromptSection.svelte';
	import type { Tab } from '$lib/types/tabs';
	import type { DirectorCapabilities } from '$lib/types/videoDirector';
	import type { MusicDirectorCapabilities } from '$lib/types/musicDirector';

	export let tab: Tab;
	export let tabHandlers: {
		handlePromptChange: (prompt: string) => void;
		handlePromptSegmentsChange: (segments: any[]) => void;
		handleNegativePromptChange: (prompt: string) => void;
		handleNegativePromptSegmentsChange: (segments: any[]) => void;
		handlePromptTabsChange: (promptTabs: any[]) => void;
		handleActivePromptTabChange: (activePromptTab: number) => void;
	};
	export let promptRelayActive: boolean;
	export let videoDirectorActive: boolean;
	export let videoDirectorCaps: DirectorCapabilities | null;
	export let musicDirectorActive: boolean;
	export let musicDirectorCaps: MusicDirectorCapabilities | null;
	export let numPrompts: number;
	export let negativePromptSupported: boolean;
	export let negativeInert: boolean;
	export let onClose: () => void;

	$: segmentCount = tab.promptSegments?.length ?? 0;
</script>

<StudioSheet maxHeight="88%" ariaLabel="Prompt" on:close={onClose}>
	<svelte:fragment slot="header">
		<div class="flex items-baseline gap-2">
			<span class="text-sm font-semibold text-fg">Prompt</span>
			<span class="font-mono text-2xs text-fg-subtle"
				>{segmentCount} {segmentCount === 1 ? 'SEGMENT' : 'SEGMENTS'}</span
			>
		</div>
	</svelte:fragment>

	<div class="pb-4">
		<PromptSection
			{tab}
			{tabHandlers}
			{promptRelayActive}
			{videoDirectorActive}
			{videoDirectorCaps}
			{musicDirectorActive}
			{musicDirectorCaps}
			{numPrompts}
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
