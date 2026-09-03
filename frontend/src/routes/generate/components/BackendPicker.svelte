<script lang="ts">
	import { api } from '$lib/services/api/index';
	import { tabsStore } from '$lib/stores/tabs';
	import { logger } from '$lib/utils/logger';
	import { Badge, Spinner } from '$lib/components/ui';
	import type { RequirementBackendInfo } from '$lib/types/api';

	// Pins this tab's `selectedBackendId` to one of the current preset's engine's
	// enabled backends, or leaves it `null` ("Automatic") for the Generation
	// Router to pick. Renders nothing when the engine has one or zero enabled
	// backends - there is no choice to make. Backend eligibility comes from
	// the same `backends[]` the requirements tab uses
	// (`backend_infos_for_engine` - exactly the router's candidate set), so
	// the chip shown here always matches what routing would actually see.
	export let tabId: string;
	export let presetId: string | null | undefined = undefined;
	export let engine: string | undefined = undefined;
	export let selectedBackendId: string | null | undefined = undefined;
	/** Fires whenever the picker learns whether there is more than one
	 *  candidate backend - lets the page hide the "Runs on…" pre-flight line
	 *  when there's no real choice to explain. */
	export let onEligibilityChange: ((visible: boolean) => void) | undefined = undefined;

	let backends: RequirementBackendInfo[] = [];
	let loading = false;
	let loadedForPresetId: string | null | undefined = null;

	function verdict(summary: RequirementBackendInfo['summary']): { tone: 'success' | 'warning' | 'danger'; label: string } {
		if (summary.missing > 0) return { tone: 'danger', label: `${summary.missing} missing` };
		if (summary.unknown > 0) return { tone: 'warning', label: `${summary.unknown} unknown` };
		if (summary.optional_missing > 0) return { tone: 'warning', label: `${summary.optional_missing} optional` };
		return { tone: 'success', label: 'ok' };
	}

	async function load(id: string) {
		loading = true;
		loadedForPresetId = id;
		try {
			const response = await api.getPresetRequirements(id);
			backends = response.success && response.data ? response.data.backends || [] : [];
		} catch (error) {
			logger.error('Failed to load backends for the backend picker:', error);
			backends = [];
		} finally {
			loading = false;
			onEligibilityChange?.(backends.length > 1);
		}
	}

	$: if (presetId && presetId !== loadedForPresetId) {
		void load(presetId);
	} else if (!presetId && loadedForPresetId !== null) {
		backends = [];
		loadedForPresetId = null;
		onEligibilityChange?.(false);
	}

	function select(id: string | null) {
		if (id === (selectedBackendId ?? null)) return;
		tabsStore.updateTab(tabId, { selectedBackendId: id });
	}
</script>

{#if backends.length > 1}
	<section>
		<h3 class="label">Backend</h3>
		<p class="text-xs text-fg-subtle mb-2">Which backend runs this tab's generations.</p>
		<div
			class="rounded-lg border border-line overflow-hidden divide-y divide-line"
			role="listbox"
			aria-label="Backend"
		>
			<button
				type="button"
				role="option"
				aria-selected={!selectedBackendId}
				class="w-full flex items-center gap-2 px-3 py-2 text-left {!selectedBackendId ? 'bg-signal/10' : 'hover:bg-surface-2'}"
				on:click={() => select(null)}
			>
				<span class="text-sm font-medium flex-1 truncate {!selectedBackendId ? 'text-signal' : 'text-fg'}"
					>Automatic</span
				>
				<span class="text-xs text-fg-subtle">Router picks</span>
			</button>
			{#each backends as backend (backend.id)}
				{@const isSelected = backend.id === selectedBackendId}
				{@const chip = verdict(backend.summary)}
				<button
					type="button"
					role="option"
					aria-selected={isSelected}
					class="w-full flex items-center gap-2 px-3 py-2 text-left {isSelected ? 'bg-signal/10' : 'hover:bg-surface-2'}"
					on:click={() => select(backend.id)}
				>
					<span class="text-sm font-medium truncate {isSelected ? 'text-signal' : 'text-fg'}">{backend.name}</span>
					{#if engine}<span class="font-mono text-2xs uppercase text-fg-subtle flex-shrink-0">{engine}</span>{/if}
					{#if backend.is_default}<span class="font-mono text-2xs text-fg-subtle flex-shrink-0">default</span>{/if}
					<span class="flex-1"></span>
					<Badge variant={chip.tone} size="sm">{chip.label}</Badge>
				</button>
			{/each}
		</div>
		{#if loading}
			<div class="flex justify-center py-2"><Spinner size="sm" /></div>
		{/if}
	</section>
{/if}
