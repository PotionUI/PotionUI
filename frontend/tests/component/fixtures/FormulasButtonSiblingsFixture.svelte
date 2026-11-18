<script lang="ts">
	import FormulasButton from '$lib/components/formulas/FormulasButton.svelte';

	let { layout }: { layout: 'rail' | 'sheet' | 'dock' } = $props();

	const tab = { id: 'tab', selectedPreset: 'preset', selectedMode: 'mode', formData: {} } as never;
</script>

<div data-row>
	<FormulasButton {tab} {layout} presetName="Preset" presetVersion="1" modeLabel="Mode" />
	<span data-after-changed>Changed</span>
	<button type="button" data-after-settings>Settings</button>
</div>
