<script lang="ts">
	import { untrack } from 'svelte';
	import { Alert, Button } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import type { ModeConsentRequest, ModesGrantBody } from '$lib/services/api/recipeModes';
	import {
		buildGrantBody,
		computeTotals,
		freeHere,
		installBlocker,
		sizeLabel,
		type ModeInstallState
	} from './modeInstallModel';

	let {
		request,
		busy = false,
		error = '',
		cancelBusy = false,
		onApprove,
		onCancelRun
	}: {
		request: ModeConsentRequest;
		busy?: boolean;
		error?: string;
		cancelBusy?: boolean;
		onApprove: (body: ModesGrantBody) => unknown;
		onCancelRun: () => void;
	} = $props();

	let choice = $state<ModeInstallState>(
		untrack(() => ({
			modes: Object.fromEntries(request.modes.map((mode) => [mode.id, mode.ready || mode.locked || mode.selected])),
			extras: {},
			variants: {},
			targetId: request.target.backend_id
		}))
	);

	const included = $derived(request.modes.filter((mode) => mode.locked || mode.selected || mode.ready));
	const optional = $derived(request.modes.filter((mode) => !(mode.locked || mode.selected || mode.ready)));
	const totals = $derived(computeTotals(request, choice));
	const blocker = $derived(installBlocker(request, choice));
	const free = $derived(freeHere(request));
	const pickedCount = $derived(optional.filter((mode) => choice.modes[mode.id]).length);

	function toggle(id: string) {
		choice.modes[id] = !choice.modes[id];
	}
</script>

<div class="space-y-3 rounded border border-signal/30 bg-signal/5 px-3 py-3" data-mode-wizard-consent>
	<div>
		<p class="text-sm text-fg-muted">To continue, this step needs to download the following:</p>
		<ul class="mt-2 space-y-1">
			{#each included as mode (mode.id)}
				<li class="flex items-baseline gap-2 text-sm">
					<Icon name="check" className="h-3.5 w-3.5 flex-shrink-0 text-signal" />
					<span class="font-medium text-fg">{mode.label}</span>
					{#if mode.short_description}<span class="min-w-0 truncate text-fg-muted">{mode.short_description}</span>{/if}
				</li>
			{/each}
		</ul>
	</div>

	{#if optional.length > 0}
		<details class="rounded border border-line bg-surface-1" data-add-more-modes>
			<summary class="cursor-pointer px-3 py-2 text-sm font-medium text-fg-muted">
				Add more modes{pickedCount > 0 ? ` (${pickedCount} picked)` : ''}
			</summary>
			<ul class="border-t border-line">
				{#each optional as mode (mode.id)}
					<li class="border-t border-line first:border-t-0">
						<label class="flex cursor-pointer items-center gap-3 px-3 py-1.5 hover:bg-surface-2" data-wizard-mode={mode.id}>
							<input
								type="checkbox"
								class="h-4 w-4 accent-signal-solid"
								checked={!!choice.modes[mode.id]}
								disabled={busy}
								onchange={() => toggle(mode.id)}
							/>
							<span class="min-w-0 flex-1 truncate text-sm text-fg">
								{mode.label}
								{#if mode.short_description}<span class="ml-1 text-fg-muted">{mode.short_description}</span>{/if}
							</span>
							<span class="font-mono text-sm tabular-nums text-fg-muted">+ {sizeLabel(mode.missing_bytes)}</span>
						</label>
					</li>
				{/each}
			</ul>
		</details>
	{/if}

	<div class="flex items-center justify-between border-t border-signal/20 pt-2 font-mono text-sm tabular-nums">
		<span class="text-fg-muted">To download <span class="text-fg" data-wizard-total>{sizeLabel(totals.bytes)}</span></span>
		{#if free != null}<span class="text-fg-muted">Free <span class="text-fg">{sizeLabel(free)}</span></span>{/if}
	</div>

	{#if error}
		<p class="text-sm text-danger" data-mode-install-error>{error}</p>
	{:else if blocker}
		<Alert variant="warning" density="compact"><span data-mode-blocker>{blocker}</span></Alert>
	{/if}

	<div class="flex items-center justify-between gap-3 pt-1">
		<Button
			size="sm"
			variant="primary"
			loading={busy}
			disabled={!!blocker}
			onclick={() => onApprove(buildGrantBody(request, choice))}
		>
			Approve and download
		</Button>
		<button
			type="button"
			class="text-xs text-fg-subtle underline decoration-dotted hover:text-fg-muted disabled:opacity-50"
			disabled={cancelBusy}
			onclick={onCancelRun}
		>
			Cancel setup instead
		</button>
	</div>
</div>
