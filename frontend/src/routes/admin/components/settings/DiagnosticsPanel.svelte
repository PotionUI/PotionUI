<script lang="ts">
	import { DetailSection } from '$lib/components/detail';
	import RuntimeSettingRow from './RuntimeSettingRow.svelte';
	import {
		runtimeSetting,
		runtimeSettingsFor,
		runtimeSettingValue,
		type RuntimeSettingDescriptor
	} from './runtimeSettings';

	let {
		settings,
		onSettingChange
	}: { settings: Record<string, any>; onSettingChange: (key: string, value: unknown) => void } = $props();

	const profiling = runtimeSettingsFor('diagnostics');

	function parentOff(descriptor: RuntimeSettingDescriptor): boolean {
		if (!descriptor.parent) return false;
		const parent = runtimeSetting(descriptor.parent);
		return parent !== undefined && runtimeSettingValue(parent, settings) !== true;
	}
</script>

<DetailSection label="Performance profiling" padded={false}>
	<div class="px-4 sm:px-5 divide-y divide-line">
		{#each profiling as descriptor (descriptor.key)}
			<RuntimeSettingRow {descriptor} {settings} {onSettingChange} disabled={parentOff(descriptor)} />
		{/each}
	</div>
</DetailSection>
