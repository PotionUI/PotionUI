<script lang="ts">
	/**
	 * The generic artifact host for an ordinary user's generation detail: it
	 * reads the generation's persisted run report and renders every artifact it
	 * recorded through `artifactRendererRegistry`, so a plugin-contributed
	 * `history.artifact` renderer reaches history the same way it reaches the
	 * admin run-report page. Types with no renderer fall back to the raw
	 * payload; artifacts the recorder dropped show why instead.
	 */
	import { api } from '$lib/services/api/index';
	import { logger } from '$lib/utils/logger';
	import { artifactRendererRegistry } from '$lib/registries/artifactRendererRegistry';
	import { frontendHooks } from '$lib/stores/plugins';
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

	// The report fetch and every renderer resolve it starts are asynchronous and
	// unordered. Without this counter a resolve for a generation the modal has
	// already moved off arrives last and paints the previous generation's
	// artifacts under the current one's heading.
	let loadToken = 0;

	$effect(() => {
		const id = generationId;
		const token = ++loadToken;

		artifacts = [];
		promptTemplate = null;
		renderers = {};

		if (id) void loadReport(id, token);
	});

	async function loadReport(id: string, token: number): Promise<void> {
		try {
			const response = await api.getGenerationRunReport(id);
			if (token !== loadToken) return;
			const report = response.success ? (response.data?.run_report ?? null) : null;
			artifacts = report?.artifacts ?? [];
			promptTemplate = report?.prompt_template ?? null;
		} catch (error) {
			if (token !== loadToken) return;
			logger.error('Failed to load run report artifacts:', error);
		}
	}

	$effect(() => {
		const types = [
			...new Set(artifacts.filter((a) => !a.omitted).map((a) => a.artifact_type))
		];
		// The renderer registry is a plain map, so it can't be depended on
		// directly; `frontendHooks` is set by every applied plugin-extension
		// snapshot, which is the same moment a plugin renderer appears or goes
		// away. Reading it here re-resolves on a plugin refresh.
		void $frontendHooks;
		void resolveRenderers(types, loadToken);
	});

	async function resolveRenderers(types: string[], token: number): Promise<void> {
		const resolved: Record<string, any> = {};
		for (const type of types) {
			resolved[type] = (await artifactRendererRegistry.resolve(type)) ?? FallbackArtifact;
		}
		if (token !== loadToken) return;
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
