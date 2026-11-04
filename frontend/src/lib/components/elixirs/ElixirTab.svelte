<script lang="ts">
	import { tabsStore } from '$lib/stores/tabs';
	import type { ToolTabComponentProps } from '$lib/tool-tabs/types';
	import { rememberElixir, type ElixirRef, type ElixirTabState } from '$lib/elixirs/tab';
	import { elixirTabStatus } from '$lib/elixirs/tabStatus';
	import type { Run } from '$lib/elixirs/types';
	import ElixirEditor, { type ElixirStep } from './ElixirEditor.svelte';
	import ElixirPicker from './ElixirPicker.svelte';

	let { host }: ToolTabComponentProps = $props();

	let elixirId = $derived(host.key ?? (host.state as Partial<ElixirTabState> | null)?.elixirId ?? null);

	$effect(() => {
		if (elixirId) rememberElixir(elixirId);
	});

	function report(run: Run | null, steps: ElixirStep[]) {
		host.setStatus?.(elixirTabStatus(run, steps));
	}

	function choose(elixir: ElixirRef) {
		host.claimKey?.(elixir.id, { elixirId: elixir.id } satisfies ElixirTabState, elixir.name ?? null);
	}

	function lost() {
		tabsStore.updateToolTab(host.tabId, { key: null, state: null, status: null });
	}
</script>

{#if elixirId}
	{#key elixirId}
		<ElixirEditor id={elixirId} tabId={host.tabId} hostPhone={host.phone} ontitle={(name) => host.setTitle(name)} ondirty={(edits) => host.setDirty(edits)} onstatus={report} onlost={lost} />
	{/key}
{:else}
	<div class="dot-grid flex min-h-0 flex-1 justify-center overflow-y-auto px-3 pb-6 pt-14" data-testid="elixir-chooser">
		<div class="w-full max-w-[440px]">
			<h2 class="text-center text-lg font-semibold text-fg">Open an elixir</h2>
			<p class="mb-4 mt-1 text-center text-sm text-fg-muted">Pick one to work on, or start a new one.</p>
			<div class="rounded-xl border border-line-strong bg-surface-2 p-1 shadow-floating">
				<ElixirPicker close={() => {}} onopen={choose} />
			</div>
		</div>
	</div>
{/if}
