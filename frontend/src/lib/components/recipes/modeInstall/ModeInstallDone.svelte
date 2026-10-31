<script lang="ts">
	import { goto } from '$app/navigation';
	import Icon from '$lib/components/Icon.svelte';
	import AssignmentCard from '$lib/components/assignment/AssignmentCard.svelte';
	import { createPresetAssignmentAdapter } from '$lib/components/assignment/presetAssignmentAdapter';
	import { tabsStore } from '$lib/stores/tabs';
	import { joinLabels, sizeLabel } from './modeInstallModel';

	let {
		presetId,
		presetName,
		modes,
		bytes = null,
		showAccess = true
	}: {
		presetId: string;
		presetName: string;
		modes: { id: string; label: string; description?: string | null }[];
		bytes?: number | null;
		showAccess?: boolean;
	} = $props();

	function tryMode(mode: { id: string; label: string }) {
		tabsStore.addTabWithData(mode.label, { selectedPreset: presetId, selectedMode: mode.id });
		void goto('/generate');
	}
</script>

<div class="space-y-3" data-mode-install-done>
	<div class="flex items-start gap-3 rounded-lg border border-success/30 bg-success/10 px-4 py-3">
		<Icon name="check" className="mt-0.5 h-4 w-4 shrink-0 text-success" />
		<div class="min-w-0">
			<p class="text-base font-semibold text-fg">
				{modes.length > 0 ? `Ready: ${joinLabels(modes.map((mode) => mode.label))}` : 'Everything is ready'}
			</p>
			<p class="text-sm text-fg-muted">
				{bytes ? `${sizeLabel(bytes)} downloaded and checked. ` : ''}Default files were filled in for these modes.
			</p>
		</div>
	</div>

	{#if modes.length > 0}
		<div class="rounded-lg border border-line bg-surface-1">
			<div class="border-b border-line px-5 py-3 font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle">
				Try a mode
			</div>
			<ul class="divide-y divide-line">
				{#each modes as mode (mode.id)}
					<li class="flex items-center justify-between gap-3 px-5 py-2.5" data-try-mode={mode.id}>
						<div class="min-w-0">
							<div class="text-base text-fg">{mode.label}</div>
							{#if mode.description}
								<div class="truncate text-sm text-fg-muted">{mode.description}</div>
							{/if}
						</div>
						<button
							type="button"
							class="inline-flex shrink-0 items-center gap-1 text-base text-signal hover:underline"
							onclick={() => tryMode(mode)}
						>
							Try it <Icon name="arrow-right" className="h-3.5 w-3.5" />
						</button>
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#if showAccess && presetId}
		{#key presetId}
			<AssignmentCard
				adapter={createPresetAssignmentAdapter(presetId)}
				resourceKey={presetId}
				resourceName={presetName}
			/>
		{/key}
	{/if}
</div>
