<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge, Spinner, Alert } from '$lib/components/ui';
	import { confidenceDisplay, digestConflictTooltip } from '$lib/utils/modelAvailability';
	import { timeAgo } from '$lib/utils/relativeTime';
	import type { ModelAvailabilityResponse } from '$lib/types/models';
	import { formatBytes, formatDate } from './formatters';

	export let availability: ModelAvailabilityResponse | null = null;
	export let loading: boolean = false;
	/** The model's canonical sha256, for the `conflict` badge tooltip's "expected" value. */
	export let expectedDigest: string | null | undefined = undefined;
</script>

<div class="bg-surface-2 rounded-lg p-3">
	<div class="flex items-center gap-2 mb-2">
		<Icon name="database" className="w-4 h-4 text-fg-muted" />
		<h3 class="text-sm font-semibold text-fg">Availability</h3>
		{#if availability && availability.availability.length > 0}
			<span class="font-mono tabular-nums text-2xs text-fg-subtle">
				({availability.availability.length})
			</span>
		{/if}
	</div>

	{#if loading}
		<div class="flex items-center justify-center py-4">
			<Spinner size="sm" />
		</div>
	{:else if availability}
		{#if availability.digest_conflict}
			<Alert variant="danger" density="compact" icon class="mb-3">
				At least one backend's copy of this file does not match the expected
				content digest and has been excluded from routing. Re-sync or replace
				the file on that backend, then re-index it.
			</Alert>
		{/if}

		{#if availability.size_conflict}
			<Alert variant="warning" density="compact" icon class="mb-3">
				Backends report this filename at different sizes - they most likely hold
				different weights, not the same model. Verify before relying on this to
				always generate the same result.
			</Alert>
		{/if}

		{#if availability.availability.length === 0}
			{#if availability.indexed}
				<p class="text-sm text-fg-subtle italic">
					No indexed backend can currently load this model.
				</p>
			{:else}
				<p class="text-xs text-fg-subtle">
					Nothing has been indexed yet, on any backend - this isn't necessarily
					unavailable, just unknown. Index a backend from
					<a href="/admin?tab=backends" class="text-signal hover:underline">Admin &rarr; Backends</a>.
				</p>
			{/if}
		{:else}
			<div class="space-y-2">
				{#each availability.availability as entry (entry.id)}
					{@const cd = confidenceDisplay(entry.confidence)}
					<div class="rounded border border-line-strong p-2 text-xs space-y-1.5">
						<div class="flex items-center justify-between gap-2">
							<span class="font-medium text-fg truncate" title={entry.backend_name}>
								{entry.backend_name}
							</span>
							<div class="flex items-center gap-1 flex-shrink-0">
								{#if entry.engine}
									<Badge variant="neutral" size="sm" class="font-mono uppercase">
										{entry.engine}
									</Badge>
								{/if}
								{#if entry.confidence === 'conflict'}
									<Tooltip
										text={digestConflictTooltip(entry.digest, expectedDigest)}
										position="top"
									>
										<Badge variant={cd.variant} size="sm">{cd.label}</Badge>
									</Tooltip>
								{:else}
									<Badge variant={cd.variant} size="sm">{cd.label}</Badge>
								{/if}
							</div>
						</div>
						<div
							class="font-mono text-2xs text-fg-muted truncate"
							title={entry.ref}
						>
							{entry.ref}
						</div>
						<div class="flex items-center justify-between text-2xs text-fg-subtle">
							<span class="font-mono tabular-nums">
								{entry.size ? formatBytes(entry.size) : 'Unknown size'}
							</span>
							{#if entry.indexed_at}
								<span class="font-mono tabular-nums" title={formatDate(entry.indexed_at)}>
									Indexed {timeAgo(entry.indexed_at)}
								</span>
							{/if}
						</div>
					</div>
				{/each}
			</div>
		{/if}
	{:else}
		<p class="text-sm text-fg-subtle italic">Unable to load availability.</p>
	{/if}
</div>
