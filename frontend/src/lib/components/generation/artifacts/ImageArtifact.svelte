<script lang="ts">
	import { api } from '$lib/services/api';
	import { resolveArtifactImageSrc, type ArtifactImageValue } from '$lib/generation/artifacts/mediaRef';

	export let artifact: { artifact_data: { label?: string; image?: ArtifactImageValue } };
</script>

<div class="space-y-3">
	<!-- Image label header -->
	<div class="flex items-center gap-2 p-2 bg-surface-2/50 border border-line/60">
		<div class="p-1 bg-surface-3">
			<svg class="w-3.5 h-3.5 text-fg" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
			</svg>
		</div>
		<span class="text-sm font-semibold text-fg-muted">
			{artifact.artifact_data.label || 'Image'}
		</span>
	</div>

	<!-- Image display -->
	<div class="relative overflow-hidden border border-line shadow-md hover:shadow-lg transition-shadow duration-200 bg-surface-2 flex items-center justify-center">
		{#if artifact.artifact_data.image}
			<img
				src={resolveArtifactImageSrc(artifact.artifact_data.image, api.getBaseURL())}
				alt={artifact.artifact_data.label || 'Artifact Image'}
				class="max-w-full max-h-[400px] object-contain"
			/>
		{:else}
			<div class="p-4 text-center text-fg-subtle">
				<svg class="w-12 h-12 mx-auto mb-2 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
				</svg>
				<p class="text-sm">Image not available</p>
			</div>
		{/if}
	</div>
</div>
