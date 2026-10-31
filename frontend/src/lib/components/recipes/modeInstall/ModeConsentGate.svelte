<script lang="ts">
	import { Button } from '$lib/components/ui';
	import type { ModeConsentRequest, ModesGrantBody } from '$lib/services/api/recipeModes';
	import ModeInstallSheet from './ModeInstallSheet.svelte';

	let {
		request,
		preselect = [],
		busy = false,
		error = '',
		cancelBusy = false,
		onApprove,
		onCancelRun,
		onReplan
	}: {
		request: ModeConsentRequest;
		preselect?: string[];
		busy?: boolean;
		error?: string;
		cancelBusy?: boolean;
		onApprove: (body: ModesGrantBody) => unknown;
		onCancelRun: () => void;
		onReplan?: (target: string, modes: string[]) => Promise<ModeConsentRequest | null>;
	} = $props();

	let open = $state(true);
</script>

<div class="rounded border border-signal/30 bg-signal/5 px-3 py-3" data-mode-consent-gate>
	<p class="text-sm font-semibold text-signal">Needs your go-ahead</p>
	<p class="mt-0.5 text-sm text-fg-muted">Choose which modes to install, then approve the download.</p>
	<div class="mt-3 flex items-center gap-3">
		<Button size="sm" variant="primary" onclick={() => (open = true)}>Review and install</Button>
		<Button size="sm" variant="secondary" loading={cancelBusy} onclick={onCancelRun}>Cancel setup</Button>
	</div>
</div>

{#if open}
	<ModeInstallSheet
		{request}
		{preselect}
		{busy}
		{error}
		{onReplan}
		onConfirm={onApprove}
		onCancel={() => (open = false)}
	/>
{/if}
