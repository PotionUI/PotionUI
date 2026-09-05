<script lang="ts">
	/**
	 * The generic artifact host for an ordinary user's generation detail: it
	 * reads the generation's persisted run report and renders every artifact it
	 * recorded through `artifactRendererRegistry`, so a plugin-contributed
	 * `history.artifact` renderer reaches history the same way it reaches the
	 * admin run-report page. Types with no renderer fall back to the raw
	 * payload; artifacts the recorder dropped show why instead.
	 */
	import { onDestroy } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { logger } from '$lib/utils/logger';
	import { artifactRendererRegistry } from '$lib/registries/artifactRendererRegistry';
	import { extensionRegistrations } from '$lib/plugin-api/extensionRefresh';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge } from '$lib/components/ui';
	import FallbackArtifact from './FallbackArtifact.svelte';
	import type { RunReportArtifact } from '$lib/services/admin-api';
	import '$lib/generation/artifacts/builtin';

	let { generationId }: { generationId: string } = $props();

	let artifacts = $state<RunReportArtifact[]>([]);
	let promptTemplate = $state<{ positive: string; negative: string } | null>(null);
	let renderers = $state<Record<string, any>>({});
	let expanded = $state(false);

	// A report fetch and a batch of renderer resolves are asynchronous and
	// unordered, and each has its own lifetime: the report belongs to one
	// generation, a renderer batch to one generation AND one set of plugin
	// registrations. They get a counter each, so a report arriving after the
	// modal moved on can't paint the wrong generation and a renderer batch
	// started before a plugin was disabled can't reinstate its component.
	let reportToken = 0;
	let rendererToken = 0;

	onDestroy(() => {
		reportToken++;
		rendererToken++;
	});

	$effect(() => {
		const id = generationId;
		const token = ++reportToken;
		rendererToken++;

		artifacts = [];
		promptTemplate = null;
		renderers = {};

		if (id) void loadReport(id, token);
	});

	async function loadReport(id: string, token: number): Promise<void> {
		try {
			const response = await api.getGenerationRunReport(id);
			if (token !== reportToken) return;
			const report = response.success ? (response.data?.run_report ?? null) : null;
			artifacts = report?.artifacts ?? [];
			promptTemplate = report?.prompt_template ?? null;
		} catch (error) {
			if (token !== reportToken) return;
			logger.error('Failed to load run report artifacts:', error);
		}
	}

	$effect(() => {
		const types = [
			...new Set(artifacts.filter((a) => !a.omitted).map((a) => a.artifact_type))
		];
		// The renderer registry is a plain map, so it can't be depended on
		// directly; `extensionRegistrations` ticks once per applied plugin
		// snapshot, which is the moment a renderer appears, changes revision or
		// goes away.
		void $extensionRegistrations;

		const token = ++rendererToken;
		// Dropped before the replacement resolves, so a renderer a refresh
		// removed stops being mounted at the refresh rather than whenever its
		// successor happens to arrive.
		renderers = {};
		void resolveRenderers(types, token);
	});

	async function resolveRenderers(types: string[], token: number): Promise<void> {
		const resolved: Record<string, any> = {};
		for (const type of types) {
			const component = (await artifactRendererRegistry.resolve(type)) ?? FallbackArtifact;
			if (token !== rendererToken) return;
			resolved[type] = component;
		}
		if (token !== rendererToken) return;
		renderers = resolved;
	}

	function pipeKey(artifact: RunReportArtifact): string {
		return artifact.pipe_id != null ? String(artifact.pipe_id) : 'unknown';
	}

	// `rendered_prompt` only shows its per-image index once its pipe produced
	// more than one image, so the count is per pipe, not per report.
	let renderedPromptCounts = $derived.by(() => {
		const counts: Record<string, number> = {};
		for (const artifact of artifacts) {
			if (artifact.artifact_type !== 'rendered_prompt') continue;
			const key = pipeKey(artifact);
			counts[key] = (counts[key] ?? 0) + 1;
		}
		return counts;
	});
</script>

{#if artifacts.length > 0}
	<div class="bg-surface-2 rounded-lg overflow-hidden">
		<button
			type="button"
			class="flex w-full items-center justify-between px-3 py-2.5 {expanded
				? 'border-b border-line'
				: ''} transition-colors hover:bg-surface-3"
			onclick={() => (expanded = !expanded)}
			aria-expanded={expanded}
		>
			<div class="flex items-center gap-2">
				<Icon name="box" className="w-4 h-4 text-info" />
				<h3 class="text-sm font-semibold text-fg">Artifacts</h3>
			</div>
			<div class="flex items-center gap-2">
				<Badge variant="neutral" size="sm">{artifacts.length}</Badge>
				<Icon
					name="chevron-down"
					className="w-3.5 h-3.5 text-fg-subtle transition-transform {expanded ? 'rotate-180' : ''}"
				/>
			</div>
		</button>

		{#if expanded}
			<div class="p-3 space-y-2">
				{#each artifacts as artifact, index (index)}
					<div class="bg-surface-3 rounded-lg p-2.5">
						<div class="flex items-center gap-2 mb-1.5">
							<span class="font-mono text-2xs uppercase tracking-wider text-fg-disabled">
								{artifact.artifact_type.replace(/_/g, ' ')}
							</span>
							{#if artifact.pipe_id != null}
								<span class="font-mono text-2xs text-fg-subtle truncate">{artifact.pipe_id}</span>
							{/if}
						</div>

						{#if artifact.omitted}
							<p class="text-xs text-fg-subtle">
								Payload not recorded (<span class="font-mono tabular-nums"
									>{artifact.omitted.bytes}</span
								> bytes, {artifact.omitted.reason.replace(/_/g, ' ')})
							</p>
						{:else if renderers[artifact.artifact_type]}
							{@const ArtifactComponent = renderers[artifact.artifact_type]}
							<div class="text-xs text-fg-muted">
								<ArtifactComponent
									artifact={artifact as never}
									totalImages={renderedPromptCounts[pipeKey(artifact)]}
									{promptTemplate}
								/>
							</div>
						{/if}
					</div>
				{/each}
			</div>
		{/if}
	</div>
{/if}
