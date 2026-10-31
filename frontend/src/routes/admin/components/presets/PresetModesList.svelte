<script lang="ts">
	import { onMount } from 'svelte';
	import { Badge, Button } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import { api } from '$lib/services/api/index';
	import type { PresetModeInfo } from '$lib/types/api';
	import { openModeInstall } from '$lib/stores/modeInstall';
	import { onModelInstalled } from '$lib/stores/modelInstalls';
	import { modeInstallLabel } from '$lib/utils/presetModeOptions';
	import { logger } from '$lib/utils/logger';

	let { presetId }: { presetId: string } = $props();

	let modes = $state<PresetModeInfo[]>([]);
	let loadedFor = '';

	const withInstall = $derived(modes.filter((mode) => !!mode.install));
	const readyCount = $derived(withInstall.filter((mode) => mode.install?.ready).length);

	async function load(id: string) {
		try {
			const response = await api.getPresetModes(id);
			if (id !== presetId) return;
			modes = response.success && response.data ? response.data.modes : [];
		} catch (error) {
			logger.warn('Could not load the preset modes list', error);
			modes = [];
		}
	}

	$effect(() => {
		const id = presetId;
		if (id === loadedFor) return;
		loadedFor = id;
		modes = [];
		void load(id);
	});

	onMount(() => onModelInstalled(() => void load(presetId)));
</script>

{#if withInstall.length > 0}
	<DetailSection label="Modes">
		<p class="mb-3 font-mono text-sm tabular-nums text-fg-subtle" data-preset-modes-count>
			{readyCount} of {withInstall.length} ready
		</p>
		<ul class="divide-y divide-line rounded border border-line" data-preset-modes>
			{#each withInstall as mode (mode.name)}
				{@const install = mode.install!}
				<li class="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5" data-preset-mode={mode.name}>
					<div class="min-w-0 flex-1 basis-48">
						<div class="text-base text-fg">{mode.label}</div>
						{#if mode.short_description}
							<div class="text-sm text-fg-muted">{mode.short_description}</div>
						{/if}
					</div>
					{#if install.ready}
						<Badge variant="success" dot>Ready</Badge>
					{:else}
						<Badge variant="warning" dot>Not installed</Badge>
						<Button
							variant="secondary"
							size="sm"
							icon="download"
							class="font-mono tabular-nums"
							onclick={() => openModeInstall(install.recipe_id, [mode.name])}
						>
							{modeInstallLabel(install)}
						</Button>
					{/if}
				</li>
			{/each}
		</ul>
	</DetailSection>
{/if}
