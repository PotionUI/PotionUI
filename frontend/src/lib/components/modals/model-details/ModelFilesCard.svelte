<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { formatBytes } from './formatters';

	export let files: any[] = [];
</script>

{#if files.length > 0}
	<div class="bg-surface-2 rounded-lg p-3">
		<div class="flex items-center gap-2 mb-2">
			<Icon name="image" className="w-4 h-4 text-success" />
			<h3 class="text-sm font-semibold text-fg">Files</h3>
			<span class="px-2 py-0.5 rounded text-xs font-medium bg-success/10 text-success border border-success/25">
				{files.length}
			</span>
		</div>
		<div class="space-y-1.5">
			{#each files as file}
				<div class="flex items-center justify-between text-xs py-1">
					<span class="text-fg-muted capitalize">{file.file_type}</span>
					<span class="text-fg font-medium font-mono tabular-nums">{formatBytes(file.file_size)}</span>
				</div>
			{/each}
		</div>
	</div>
{/if}
