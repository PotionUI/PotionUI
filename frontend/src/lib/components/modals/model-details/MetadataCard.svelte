<script lang="ts" module>
	export interface MetadataRow {
		label: string;
		value: string;
		/** Renders `value` as a copyable code chip; clicking copy copies this
		 * exact string, which may differ from a truncated/sliced `value`. */
		copyValue?: string;
		/** Tooltip on the code chip - omit to match a row that never had one. */
		title?: string;
		/** aria-label for the copy button. Defaults to "Copy {label}", but the
		 * original wording isn't mechanical (e.g. "Copy model ID", not "Copy
		 * Model ID") so callers pass it explicitly where it differs. */
		copyLabel?: string;
		uppercase?: boolean;
	}
</script>

<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { copyText } from '$lib/utils/clipboard';
	import { toasts } from '$lib/stores/toast';

	let {
		icon,
		iconClass = 'text-fg-muted',
		title,
		rows
	}: {
		icon: string;
		iconClass?: string;
		title: string;
		rows: MetadataRow[];
	} = $props();

	/** Label of the row most recently copied, to flash its icon. */
	let copiedLabel = $state<string | null>(null);
	let copiedTimer: ReturnType<typeof setTimeout> | undefined;

	async function copyRow(row: MetadataRow) {
		const ok = await copyText(row.copyValue ?? '');
		if (ok) {
			copiedLabel = row.label;
			clearTimeout(copiedTimer);
			copiedTimer = setTimeout(() => (copiedLabel = null), 1500);
		} else {
			toasts.error('Could not copy');
		}
	}
</script>

<div class="bg-surface-2 rounded-lg p-3">
	<div class="flex items-center gap-2 mb-2">
		<Icon name={icon} className="w-4 h-4 {iconClass}" />
		<h3 class="text-sm font-semibold text-fg">{title}</h3>
	</div>
	<div class="space-y-1.5 text-xs">
		{#each rows as row (row.label)}
			<div class="flex items-center justify-between py-1 gap-2">
				<span class="text-fg-muted flex-shrink-0">{row.label}</span>
				{#if row.copyValue !== undefined}
					<div class="flex items-center gap-1 min-w-0">
						<code
							class="text-xs font-mono bg-surface-3 px-1.5 py-0.5 rounded truncate max-w-[280px] text-fg"
							title={row.title}
						>
							{row.value}
						</code>
						<button
							class="text-fg-subtle hover:text-fg-muted flex-shrink-0"
							onclick={() => copyRow(row)}
							aria-label={row.copyLabel ?? `Copy ${row.label}`}
						>
							<Icon name={copiedLabel === row.label ? 'check' : 'copy'} className="w-3 h-3" />
						</button>
					</div>
				{:else}
					<span class="font-medium font-mono tabular-nums text-fg {row.uppercase ? 'uppercase' : ''}">
						{row.value}
					</span>
				{/if}
			</div>
		{/each}
	</div>
</div>
