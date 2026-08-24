<script lang="ts">
	import { formatDate } from './formatters';
	import MetadataCard, { type MetadataRow } from './MetadataCard.svelte';

	export let modelId: string = '';
	export let modelType: string = '';
	export let createdAt: string | null | undefined = undefined;

	$: rows = [
		{ label: 'Model ID', value: `${modelId.slice(0, 8)}...`, copyValue: modelId, copyLabel: 'Copy model ID' },
		{ label: 'Type', value: modelType, uppercase: true },
		...(createdAt ? [{ label: 'Created', value: formatDate(createdAt) }] : [])
	] satisfies MetadataRow[];
</script>

<MetadataCard icon="information-circle" iconClass="text-info" title="Information" {rows} />
