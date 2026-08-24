<script lang="ts">
	// Servable URLs for generation files aren't carried on the `GenerationFile`
	// type (no persisted `url` column) - every history viewer derives them the
	// same way from the generation id + the file_path's basename
	// (GenerationDetailsModal.getImageUrl), reused here rather than duplicated.
	import type { GenerationFile } from '$lib/types/history';
	import { Badge, EmptyState } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';

	let { generationId, files }: { generationId: string; files: GenerationFile[] } = $props();

	function fileUrl(file: GenerationFile): string {
		const filename = file.file_path.split('/').pop() || file.file_path;
		return `/api/media/generations/${generationId}/${filename}`;
	}

	function thumbnail(file: GenerationFile): string | null {
		if (file.file_type === 'video') return file.thumbnail_medium ?? null;
		if (file.file_type === 'image') return file.thumbnail_medium ?? fileUrl(file);
		return null;
	}

	const TYPE_ICON: Record<string, string> = {
		video: 'video',
		audio: 'audio',
		mesh: 'cube'
	};

	let sorted = $derived([...files].sort((a, b) => Number(b.is_final) - Number(a.is_final)));
</script>

<div class="bg-surface-1 border border-line rounded-lg p-4 sm:p-5">
	<div class="flex items-center justify-between mb-3">
		<h3 class="text-sm font-medium text-fg">Outputs</h3>
		<span class="font-mono text-2xs tabular-nums text-fg-subtle uppercase tracking-[0.07em]">
			{files.length} file{files.length === 1 ? '' : 's'}
		</span>
	</div>

	{#if sorted.length === 0}
		<EmptyState icon="image" title="No output files" description="This generation produced no files." compact />
	{:else}
		<div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
			{#each sorted as file (file.id)}
				<a
					href={fileUrl(file)}
					target="_blank"
					rel="noopener noreferrer"
					class="group relative aspect-square bg-surface-2 border border-line rounded-lg overflow-hidden hover:border-line-hover transition-colors duration-100"
				>
					{#if thumbnail(file)}
						<img src={thumbnail(file)} alt={file.pipe_name ?? file.file_type} class="w-full h-full object-cover" loading="lazy" />
					{:else}
						<div class="w-full h-full flex items-center justify-center">
							<Icon name={TYPE_ICON[file.file_type] ?? 'document'} className="w-6 h-6 text-fg-subtle" />
						</div>
					{/if}

					<div
						class="absolute inset-x-0 bottom-0 flex items-center justify-between gap-1 px-2 py-1.5 bg-canvas/80 backdrop-blur-sm opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100 transition-opacity duration-100"
					>
						<span class="text-2xs font-mono text-fg truncate">{file.pipe_name ?? file.file_type}</span>
						{#if file.is_final}
							<Badge variant="signal" size="sm">final</Badge>
						{/if}
					</div>
				</a>
			{/each}
		</div>
	{/if}
</div>
