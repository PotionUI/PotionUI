<script lang="ts">
	import { probeUpdates } from './probeCounter';

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	function bump(..._deps: unknown[]) {
		probeUpdates.count += 1;
	}

	$: bump(config, value);
</script>

<button type="button" on:click={() => name && onChange(name, (Number(value) || 0) + 1)}>{config.title}: {value}</button>
