<script lang="ts">
	// Legacy-mode (`export let`, `$:`) stand-in for src/routes/generate/+page.svelte:
	// the page keeps a per-tab checked map, writes it from the editor's
	// `onCheckedChange` callback, and reads it back in a reactive statement
	// (the memory-advisory preview does exactly this). Two legacy passthrough
	// layers below mirror GenerationPanels.svelte -> PromptSection.svelte.
	import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
	import Passthrough from './DirectorCheckedLoopPassthrough.svelte';
	export let value: VideoDirectorValue | undefined;
	export let capabilities: DirectorCapabilities;
	export let formData: Record<string, unknown> | null = null;
	export let onChange: (v: VideoDirectorValue) => void;
	export let tabs: { id: string }[] = [{ id: 't1' }];
	let mirrorCalls = 0;
	let checkedByTab: Record<string, Set<string>> = {};
	function handleDirectorCheckedChange(tabId: string, checked: Set<string>) {
		mirrorCalls += 1;
		checkedByTab = { ...checkedByTab, [tabId]: checked };
	}
	$: preview = checkedByTab['t1'] ?? new Set<string>();
</script>

{#each tabs as tab (tab.id)}
	<Passthrough {value} {capabilities} {formData} {onChange} onDirectorCheckedChange={(checked) => handleDirectorCheckedChange(tab.id, checked)} />
{/each}
<p data-testid="preview-count">{preview.size}</p>
<p data-testid="mirror-calls">{mirrorCalls}</p>
