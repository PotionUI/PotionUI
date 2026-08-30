<script lang="ts">
	import StudioSheet from './StudioSheet.svelte';
	import GenerationFormPane from '../GenerationFormPane.svelte';
	import type DynamicForm from '$lib/components/DynamicForm.svelte';
	import type { Tab } from '$lib/types/tabs';

	export let tab: Tab;
	export let presetName: string | undefined = undefined;
	export let modeLabel: string | undefined = undefined;
	export let videoDirectorActive: boolean;
	export let onFormDataChange: (data: Record<string, unknown>) => void;
	export let dynamicFormRefs: Record<string, DynamicForm>;
	export let onClose: () => void;
</script>

<StudioSheet maxHeight="88%" ariaLabel="Settings" on:close={onClose}>
	<svelte:fragment slot="header">
		<div class="flex items-baseline gap-2">
			<span class="text-sm font-semibold text-fg">Settings</span>
			{#if presetName}
				<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle"
					>{presetName}{#if modeLabel} · {modeLabel}{/if}</span
				>
			{/if}
		</div>
	</svelte:fragment>

	<div class="pb-4">
		{#if tab.selectedPreset && tab.selectedMode}
			<GenerationFormPane
				bind:formRef={dynamicFormRefs[tab.id]}
				{tab}
				{videoDirectorActive}
				{onFormDataChange}
			/>
		{/if}
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
