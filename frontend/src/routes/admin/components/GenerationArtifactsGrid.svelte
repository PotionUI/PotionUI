<script lang="ts">
	// Artifacts grouped by pipe (same grouping the timeline and status log
	// use), rendered as an always-visible card grid - the full page has room,
	// so there's no reason to hide these behind the drawer's expand toggle.
	import type { RunReportArtifact } from '$lib/services/admin-api';
	import { artifactRendererRegistry } from '$lib/registries/artifactRendererRegistry';
	import FallbackArtifact from '$lib/components/generation/artifacts/FallbackArtifact.svelte';
	import '$lib/generation/artifacts/builtin';
	import { artifactsForPipe, type GroupedStatusEntry } from './runReport';

	let {
		byPipe,
		artifacts,
		promptTemplate
	}: {
		byPipe: Map<string, GroupedStatusEntry[]>;
		artifacts: RunReportArtifact[];
		promptTemplate: { positive: string; negative: string } | null;
	} = $props();

	let pipeSections = $derived(
		[...byPipe.entries()]
			.map(([pipeKey, items]) => ({
				pipeKey,
				pipeLabel: items[0]?.pipeLabel ?? pipeKey,
				artifacts: artifactsForPipe(artifacts, pipeKey)
			}))
			.filter((section) => section.artifacts.length > 0)
	);
</script>

{#if pipeSections.length > 0}
	<div class="bg-surface-1 border border-line rounded-lg p-4 sm:p-5 space-y-5">
		<h3 class="text-sm font-medium text-fg">Artifacts</h3>

		{#each pipeSections as section (section.pipeKey)}
			{@const totalRenderedPrompts = section.artifacts.filter((a) => a.artifact_type === 'rendered_prompt').length}
			<div>
				<p class="text-2xs font-mono uppercase tracking-[0.07em] text-fg-subtle mb-2">{section.pipeLabel}</p>
				<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
					{#each section.artifacts as artifact, i (i)}
						<div
							class="bg-surface-2/60 border border-line rounded-lg p-3 {artifact.artifact_type === 'compare_images'
								? 'sm:col-span-2'
								: ''}"
						>
							<div class="mb-2">
								<span class="text-2xs font-mono uppercase tracking-[0.07em] text-fg-subtle">
									{artifact.artifact_type.replace(/_/g, ' ')}
								</span>
							</div>
							<div class="text-xs text-fg-muted">
								{#await artifactRendererRegistry.resolve(artifact.artifact_type) then resolvedComponent}
									{@const ArtifactComponent = resolvedComponent ?? FallbackArtifact}
									<ArtifactComponent artifact={artifact as never} totalImages={totalRenderedPrompts} {promptTemplate} />
								{/await}
							</div>
						</div>
					{/each}
				</div>
			</div>
		{/each}
	</div>
{/if}
