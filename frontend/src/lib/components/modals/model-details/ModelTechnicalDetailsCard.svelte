<script lang="ts">
	import { formatBytes, formatDate } from './formatters';
	import MetadataCard, { type MetadataRow } from './MetadataCard.svelte';

	export let filename: string = '';
	export let filePath: string | null | undefined = undefined;
	export let sha256: string | null | undefined = undefined;
	export let fileSize: number | null | undefined = undefined;
	export let indexedAt: string | null | undefined = undefined;

	$: rows = [
		{ label: 'Filename', value: filename, copyValue: filename, title: filename, copyLabel: 'Copy filename' },
		...(filePath
			? [{ label: 'File Path', value: filePath, copyValue: filePath, title: filePath, copyLabel: 'Copy file path' }]
			: []),
		...(sha256
			? [
					{
						label: 'SHA256',
						value: `${sha256.slice(0, 12)}...`,
						copyValue: sha256,
						title: sha256,
						copyLabel: 'Copy SHA256'
					}
				]
			: []),
		{ label: 'Size', value: formatBytes(fileSize) },
		...(indexedAt ? [{ label: 'Indexed', value: formatDate(indexedAt) }] : [])
	] satisfies MetadataRow[];
</script>

<MetadataCard icon="shield" title="Technical Details" {rows} />
